from __future__ import annotations
import json
import os
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

import pandas as pd
from flask import (Flask, flash, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import processing

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
UPLOAD_FOLDER = BASE_DIR / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)

db.init_db()

app = Flask(__name__)
app.secret_key = db.get_or_create_secret_key()
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB

ACCOUNT_CHOICES = [
    'Barclaycard 0%',
    'Current',
    'NatWest 0%',
    'Credit Card',
    'Tesco 0%',
]

ALL_COLUMNS = [
    ('date',         'Date'),
    ('description',  'Description'),
    ('account_name', 'Account'),
    ('value',        'Amount'),
    ('company',      'Company'),
    ('category',     'Category'),
    ('source_file',  'Source File'),
]

# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

@app.template_filter('gbp')
def gbp_filter(v):
    if v is None:
        return '—'
    return f'£{v:,.2f}'

@app.template_filter('fmtdate')
def fmtdate_filter(v):
    if not v:
        return ''
    try:
        from datetime import datetime
        return datetime.strptime(str(v), '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return str(v)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            flash('Please log in.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def get_prefs() -> dict:
    username = session.get('username', '')
    conn = db.get_conn()
    row = conn.execute(
        'SELECT prefs_json FROM user_prefs WHERE username=?', (username,)
    ).fetchone()
    conn.close()
    defaults: dict = {
        'date_range':       '90',
        'columns':          ['date', 'description', 'account_name', 'value', 'company', 'category'],
        'category_chart':   'bar',
        'top_n_merchants':  15,
        'hide_unknowns':    False,
        'color_scheme':     'plotly',
        'page_length':      25,
    }
    if row:
        defaults.update(json.loads(row['prefs_json']))
    return defaults


def save_prefs(prefs: dict) -> None:
    username = session.get('username', '')
    conn = db.get_conn()
    conn.execute(
        'INSERT OR REPLACE INTO user_prefs (username, prefs_json) VALUES (?, ?)',
        (username, json.dumps(prefs))
    )
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return redirect(url_for('transactions') if 'username' in session else url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = db.get_conn()
        user = conn.execute(
            'SELECT password_hash FROM users WHERE username=?', (username,)
        ).fetchone()
        conn.close()
        if user and check_password_hash(user['password_hash'], password):
            session['username'] = username
            return redirect(url_for('transactions'))
        flash('Invalid username or password.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    if request.method == 'POST':
        account = request.form.get('account', '').strip()
        files   = request.files.getlist('files')

        if account not in ACCOUNT_CHOICES:
            flash('Please select a valid account.', 'danger')
            return redirect(url_for('upload'))

        valid = [f for f in files if f and f.filename.lower().endswith('.csv')]
        if not valid:
            flash('Please upload at least one CSV file.', 'danger')
            return redirect(url_for('upload'))

        # Warn about duplicates
        conn = db.get_conn()
        existing_files = {
            r[0] for r in conn.execute('SELECT DISTINCT source_file FROM transactions').fetchall()
        }
        conn.close()
        dupes = [f.filename for f in valid if f.filename in existing_files]
        if dupes:
            flash(f'Warning: these files may already be imported: {", ".join(dupes)}', 'warning')

        try:
            df, skipped = processing.process_uploads(valid, account)
        except Exception as exc:
            flash(f'Processing error: {exc}', 'danger')
            return redirect(url_for('upload'))

        if df.empty:
            flash('No transactions found in the uploaded files.', 'warning')
            return redirect(url_for('upload'))

        conn = db.get_conn()
        conn.execute(
            'INSERT INTO uploaded_batches (account, file_count, row_count) VALUES (?, ?, ?)',
            (account, len(valid), len(df))
        )
        batch_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

        conn.executemany(
            'INSERT INTO transactions '
            '(date, description, account_name, value, source_file, company, category, batch_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (
                    str(r['Date']),
                    r['Description'],
                    r['Account Name'],
                    float(r['Value']) if r['Value'] is not None else None,
                    r['Source File'],
                    r['Company'],
                    r['Category'],
                    batch_id,
                )
                for _, r in df.iterrows()
            ]
        )
        conn.commit()
        conn.close()

        unknown_count = (df['Company'] == '__Unknown').sum()
        msg = f'Imported {len(df)} transactions ({skipped} filtered). '
        if unknown_count:
            msg += f'{unknown_count} have unknown company — visit Mappings to categorise them.'
        flash(msg, 'success')
        return redirect(url_for('transactions'))

    return render_template('upload.html', account_choices=ACCOUNT_CHOICES)

# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@app.route('/transactions')
@login_required
def transactions():
    prefs = get_prefs()

    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to', '')
    sel_account      = request.args.get('account', '')
    sel_category     = request.args.get('category', '')
    show_all_dates   = request.args.get('all_dates', '')

    if not date_from and not date_to and not show_all_dates:
        days = int(prefs.get('date_range', 90))
        if days > 0:
            date_to   = date.today().isoformat()
            date_from = (date.today() - timedelta(days=days)).isoformat()

    conn = db.get_conn()
    sql    = 'SELECT id, date, description, account_name, value, source_file, company, category FROM transactions WHERE 1=1'
    params: list = []

    if date_from:
        sql += ' AND date >= ?'; params.append(date_from)
    if date_to:
        sql += ' AND date <= ?'; params.append(date_to)
    if sel_account:
        sql += ' AND account_name = ?'; params.append(sel_account)
    if sel_category:
        sql += ' AND category = ?'; params.append(sel_category)
    if prefs.get('hide_unknowns'):
        sql += " AND company != '__Unknown'"

    sql += ' ORDER BY date DESC, description'
    rows      = conn.execute(sql, params).fetchall()
    accounts  = [r[0] for r in conn.execute('SELECT DISTINCT account_name FROM transactions ORDER BY account_name')]
    categories = [r[0] for r in conn.execute('SELECT DISTINCT category FROM transactions ORDER BY category')]
    conn.close()

    total_spend   = sum(r['value'] for r in rows if r['value'] and r['value'] > 0)
    total_credits = sum(r['value'] for r in rows if r['value'] and r['value'] < 0)
    unknown_count = sum(1 for r in rows if r['company'] == '__Unknown')

    return render_template('transactions.html',
        rows=rows,
        accounts=accounts,
        categories=categories,
        date_from=date_from,
        date_to=date_to,
        sel_account=sel_account,
        sel_category=sel_category,
        total_spend=total_spend,
        total_credits=total_credits,
        unknown_count=unknown_count,
        prefs=prefs,
        all_columns=ALL_COLUMNS,
    )

# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

@app.route('/charts')
@login_required
def charts():
    prefs = get_prefs()

    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    sel_account  = request.args.get('account', '')
    sel_category = request.args.get('category', '')

    if not date_from and not date_to:
        days = int(prefs.get('date_range', 90))
        if days > 0:
            date_to   = date.today().isoformat()
            date_from = (date.today() - timedelta(days=days)).isoformat()

    conn = db.get_conn()
    sql    = 'SELECT date, description, account_name, value, company, category FROM transactions WHERE value > 0'
    params: list = []
    if date_from:  sql += ' AND date >= ?';        params.append(date_from)
    if date_to:    sql += ' AND date <= ?';         params.append(date_to)
    if sel_account: sql += ' AND account_name = ?'; params.append(sel_account)
    if sel_category: sql += ' AND category = ?';    params.append(sel_category)
    if prefs.get('hide_unknowns'):
        sql += " AND company != '__Unknown'"

    rows      = conn.execute(sql, params).fetchall()
    accounts  = [r[0] for r in conn.execute('SELECT DISTINCT account_name FROM transactions ORDER BY account_name')]
    categories = [r[0] for r in conn.execute('SELECT DISTINCT category FROM transactions ORDER BY category')]
    conn.close()

    charts_data: dict = {}

    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        df['date']  = pd.to_datetime(df['date'])
        df['month'] = df['date'].dt.to_period('M').astype(str)

        # Monthly trend
        monthly = df.groupby('month')['value'].sum().reset_index()
        charts_data['monthly'] = {
            'x': monthly['month'].tolist(),
            'y': monthly['value'].round(2).tolist(),
        }

        # By category
        by_cat = df.groupby('category')['value'].sum().sort_values(ascending=False).reset_index()
        charts_data['by_category'] = {
            'labels': by_cat['category'].tolist(),
            'values': by_cat['value'].round(2).tolist(),
        }

        # Top N merchants
        top_n = int(prefs.get('top_n_merchants', 15))
        top_m = df.groupby('company')['value'].sum().sort_values(ascending=False).head(top_n).reset_index()
        charts_data['top_merchants'] = {
            'companies': top_m['company'].tolist(),
            'values':    top_m['value'].round(2).tolist(),
        }

        # Stacked by account per month
        by_acct  = df.groupby(['month', 'account_name'])['value'].sum().reset_index()
        months   = sorted(by_acct['month'].unique().tolist())
        acct_names = by_acct['account_name'].unique().tolist()
        stacked: dict = {}
        for a in acct_names:
            sub = by_acct[by_acct['account_name'] == a].set_index('month')['value']
            stacked[a] = [round(float(sub.get(m, 0)), 2) for m in months]
        charts_data['by_account'] = {'months': months, 'accounts': acct_names, 'data': stacked}

        # Anomalies (z-score per category, |z| > 2)
        df['z'] = df.groupby('category')['value'].transform(
            lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0.0
        )
        normal    = df[df['z'].abs() <= 2]
        anomalies = df[df['z'].abs() >  2]
        charts_data['anomalies'] = {
            'normal': {
                'x':    normal['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y':    normal['value'].round(2).tolist(),
                'text': (normal['description'] + ' [' + normal['category'] + ']').tolist(),
            },
            'anomaly': {
                'x':    anomalies['date'].dt.strftime('%Y-%m-%d').tolist(),
                'y':    anomalies['value'].round(2).tolist(),
                'text': (anomalies['description'] + ' [' + anomalies['category'] + ']').tolist(),
            },
        }

        # Monthly trend per category (for heatmap-style stacked area)
        top_cats = by_cat.head(8)['category'].tolist()
        df_top   = df[df['category'].isin(top_cats)]
        cat_month = df_top.groupby(['month', 'category'])['value'].sum().reset_index()
        cat_series: dict = {}
        for cat in top_cats:
            sub = cat_month[cat_month['category'] == cat].set_index('month')['value']
            cat_series[cat] = [round(float(sub.get(m, 0)), 2) for m in months]
        charts_data['category_trend'] = {'months': months, 'series': cat_series}

    return render_template('charts.html',
        charts_data=json.dumps(charts_data),
        date_from=date_from,
        date_to=date_to,
        sel_account=sel_account,
        sel_category=sel_category,
        accounts=accounts,
        categories=categories,
        prefs=prefs,
    )

# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

@app.route('/mappings')
@login_required
def mappings():
    conn = db.get_conn()
    company_maps  = conn.execute('SELECT id, description, company FROM company_map ORDER BY description').fetchall()
    category_maps = conn.execute('SELECT id, company, category FROM category_map ORDER BY company').fetchall()
    unknowns = conn.execute(
        "SELECT description, account_name, COUNT(*) as cnt, SUM(value) as total "
        "FROM transactions WHERE company='__Unknown' "
        "GROUP BY description ORDER BY cnt DESC"
    ).fetchall()
    all_companies  = sorted({r['company']  for r in company_maps})
    all_categories = sorted({r['category'] for r in category_maps})
    conn.close()
    return render_template('mappings.html',
        company_maps=company_maps,
        category_maps=category_maps,
        unknowns=unknowns,
        all_companies=all_companies,
        all_categories=all_categories,
    )


@app.route('/mappings/quick-map', methods=['POST'])
@login_required
def quick_map():
    description = request.form.get('description', '').strip()
    company     = request.form.get('company', '').strip()
    category    = request.form.get('category', '').strip()
    if not (description and company and category):
        flash('All three fields are required.', 'danger')
        return redirect(url_for('mappings'))
    conn = db.get_conn()
    conn.execute('INSERT OR REPLACE INTO company_map (description, company) VALUES (?, ?)', (description, company))
    conn.execute('INSERT OR REPLACE INTO category_map (company, category) VALUES (?, ?)', (company, category))
    n = conn.execute(
        "UPDATE transactions SET company=?, category=? WHERE UPPER(description)=UPPER(?)",
        (company, category, description)
    ).rowcount
    conn.commit()
    conn.close()
    flash(f'Mapped "{description}" → "{company}" → "{category}" ({n} transactions updated).', 'success')
    return redirect(url_for('mappings'))


@app.route('/mappings/company/add', methods=['POST'])
@login_required
def add_company():
    desc = request.form.get('description', '').strip()
    comp = request.form.get('company', '').strip()
    if desc and comp:
        conn = db.get_conn()
        conn.execute('INSERT OR REPLACE INTO company_map (description, company) VALUES (?, ?)', (desc, comp))
        conn.commit()
        conn.close()
        flash(f'Added: "{desc}" → "{comp}"', 'success')
    return redirect(url_for('mappings') + '#companies')


@app.route('/mappings/company/edit/<int:mid>', methods=['POST'])
@login_required
def edit_company(mid):
    desc = request.form.get('description', '').strip()
    comp = request.form.get('company', '').strip()
    if desc and comp:
        conn = db.get_conn()
        conn.execute('UPDATE company_map SET description=?, company=? WHERE id=?', (desc, comp, mid))
        conn.commit()
        conn.close()
        flash('Mapping updated.', 'success')
    return redirect(url_for('mappings') + '#companies')


@app.route('/mappings/company/delete/<int:mid>', methods=['POST'])
@login_required
def delete_company(mid):
    conn = db.get_conn()
    conn.execute('DELETE FROM company_map WHERE id=?', (mid,))
    conn.commit()
    conn.close()
    flash('Mapping deleted.', 'success')
    return redirect(url_for('mappings') + '#companies')


@app.route('/mappings/category/add', methods=['POST'])
@login_required
def add_category():
    comp = request.form.get('company', '').strip()
    cat  = request.form.get('category', '').strip()
    if comp and cat:
        conn = db.get_conn()
        conn.execute('INSERT OR REPLACE INTO category_map (company, category) VALUES (?, ?)', (comp, cat))
        conn.commit()
        conn.close()
        flash(f'Added: "{comp}" → "{cat}"', 'success')
    return redirect(url_for('mappings') + '#categories')


@app.route('/mappings/category/edit/<int:mid>', methods=['POST'])
@login_required
def edit_category(mid):
    comp = request.form.get('company', '').strip()
    cat  = request.form.get('category', '').strip()
    if comp and cat:
        conn = db.get_conn()
        conn.execute('UPDATE category_map SET company=?, category=? WHERE id=?', (comp, cat, mid))
        conn.commit()
        conn.close()
        flash('Category updated.', 'success')
    return redirect(url_for('mappings') + '#categories')


@app.route('/mappings/category/delete/<int:mid>', methods=['POST'])
@login_required
def delete_category(mid):
    conn = db.get_conn()
    conn.execute('DELETE FROM category_map WHERE id=?', (mid,))
    conn.commit()
    conn.close()
    flash('Category deleted.', 'success')
    return redirect(url_for('mappings') + '#categories')


@app.route('/mappings/reclassify', methods=['POST'])
@login_required
def reclassify():
    """Re-apply all mappings to every transaction in the DB."""
    conn = db.get_conn()
    comp_dict = {r['description'].upper().strip(): r['company']
                 for r in conn.execute('SELECT description, company FROM company_map').fetchall()}
    cat_dict  = {r['company'].upper().strip(): r['category']
                 for r in conn.execute('SELECT company, category FROM category_map').fetchall()}

    all_tx = conn.execute('SELECT id, description FROM transactions').fetchall()
    updates = []
    for tx in all_tx:
        desc     = (tx['description'] or '').upper().strip()
        company  = comp_dict.get(desc, '__Unknown')
        category = cat_dict.get(company.upper().strip(), '__Unknown')
        updates.append((company, category, tx['id']))

    conn.executemany('UPDATE transactions SET company=?, category=? WHERE id=?', updates)
    conn.commit()
    conn.close()
    flash(f'Re-classified {len(updates)} transactions.', 'success')
    return redirect(url_for('mappings'))

# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

@app.route('/prefs', methods=['GET', 'POST'])
@login_required
def prefs():
    if request.method == 'POST':
        new_prefs = {
            'date_range':      request.form.get('date_range', '90'),
            'columns':         request.form.getlist('columns') or ['date', 'description', 'value', 'company', 'category'],
            'category_chart':  request.form.get('category_chart', 'bar'),
            'top_n_merchants': int(request.form.get('top_n_merchants', 15)),
            'hide_unknowns':   'hide_unknowns' in request.form,
            'color_scheme':    request.form.get('color_scheme', 'plotly'),
            'page_length':     int(request.form.get('page_length', 25)),
        }
        save_prefs(new_prefs)
        flash('Preferences saved.', 'success')
        return redirect(url_for('prefs'))

    return render_template('prefs.html', prefs=get_prefs(), all_columns=ALL_COLUMNS)


# ---------------------------------------------------------------------------
# Admin — change password
# ---------------------------------------------------------------------------

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current  = request.form.get('current', '')
        new_pw   = request.form.get('new_password', '')
        confirm  = request.form.get('confirm', '')
        username = session['username']

        conn = db.get_conn()
        user = conn.execute('SELECT password_hash FROM users WHERE username=?', (username,)).fetchone()

        if not check_password_hash(user['password_hash'], current):
            flash('Current password is incorrect.', 'danger')
        elif len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'danger')
        elif new_pw != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            conn.execute('UPDATE users SET password_hash=? WHERE username=?',
                         (generate_password_hash(new_pw), username))
            conn.commit()
            flash('Password changed successfully.', 'success')
        conn.close()
        return redirect(url_for('change_password'))

    return render_template('change_password.html')

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)

import os
from tempfile import mkdtemp

from cs50 import SQL
from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import apology, login_required, lookup, usd

app = Flask(__name__)

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SESSION_FILE_DIR"] = mkdtemp()
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

app.jinja_env.filters["usd"] = usd

db = SQL("sqlite:///database.db")

if not os.environ.get("API_KEY"):
    raise RuntimeError("API_KEY not set")


@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/")
@login_required
def index():
    """Show portfolio of stocks"""
    user_id = session["user_id"]
    user = (db.execute('SELECT * FROM users WHERE id=:id', id=user_id))[0]
    cash = user["cash"]

    stock = 0
    rows = (db.execute('SELECT * FROM stocks WHERE user_id=:user_id', user_id=user_id))

    for row in rows:
        row["price"] = round(lookup(row["symbol"])["price"], 2)
        stock += row["price"] * row["shares"]

    return render_template("index.html", rows=rows, stock=stock, cash=cash)


@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    """Buy shares of stock"""
    if request.method == "POST":
        user_id = session["user_id"]

        symbol = request.form.get("symbol").upper()
        shares = request.form.get("shares")
        shares = int(shares) if shares else 0
        if not symbol:
            return apology("missing symbol", 404)
        if shares <= 0:
            return apology("missing shares", 404)

        data = lookup(symbol)
        if data is None:
            return apology("symbol not valid", 404)

        price = data["price"]
        company = data["company"]

        cost = round(shares * price, 2)
        cash = db.execute('SELECT cash FROM users WHERE id=:id', id=user_id)[0]["cash"]
        if cost > cash:
            db.execute(
                'INSERT INTO transactions (user_id, flag, symbol, shares, price, company, date_time)'
                'VALUES (:user_id, :flag, :symbol, :shares, :price, :company, datetime("now", "localtime"))',
                user_id=user_id, flag=0, symbol=symbol, shares=shares, price=price, company=company
            )
            return apology("insufficient cash", 404)

        cash = round(cash - cost, 2)
        db.execute('UPDATE users SET cash=:cash WHERE id=:id', cash=cash, id=user_id)
        db.execute(
            'INSERT INTO transactions (user_id, flag, symbol, shares, price, company, date_time)'
            'VALUES (:user_id, :flag, :symbol, :shares, :price, :company, datetime("now", "localtime"))',
            user_id=user_id, flag=1, symbol=symbol, shares=shares, price=price, company=company
        )

        stock = db.execute(
            'SELECT id, shares FROM stocks WHERE user_id=:user_id AND symbol=:symbol', user_id=user_id, symbol=symbol
        )
        if not stock:
            db.execute(
                'INSERT INTO stocks (user_id, symbol, shares, company)'
                'VALUES (:user_id, :symbol, :shares, :company)',
                user_id=user_id, symbol=symbol, shares=shares, company=company
            )
        else:
            db.execute(
                'UPDATE stocks SET shares=:shares WHERE id=:id', shares=(stock[0]["shares"] + shares), id=stock[0]["id"]
            )

        flash(f'Congratulations! You bought {shares} {symbol} stock(s) for ${cost}!')
        return redirect("/")
    else:
        return render_template("buy.html")


@app.route("/history")
@login_required
def history():
    """Show history of transactions"""
    user_id = session["user_id"]
    rows = db.execute('SELECT * FROM transactions WHERE user_id=:user_id', user_id=user_id)
    return render_template("history.html", rows=rows)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""
    session.clear()
    if request.method == "POST":
        if not request.form.get("username"):
            return apology("must provide username", 404)
        elif not request.form.get("password"):
            return apology("must provide password", 404)

        rows = db.execute('SELECT * FROM users WHERE username = :username', username=request.form.get("username"))
        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], request.form.get("password")):
            return apology("invalid username and/or password", 404)

        session["user_id"] = rows[0]["id"]
        return redirect("/")
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""
    session.clear()
    return redirect("/")


@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():
    """Get stock quote"""
    if request.method == "POST":
        symbol = request.form.get("symbol").upper()
        if symbol and lookup(symbol):
            data = lookup(symbol)
            return render_template("quoted.html", symbol=data["symbol"], price=data["price"])
        else:
            return apology("missing symbol", 404)
    else:
        return render_template("quote.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Register user"""
    if request.method == "POST":
        session.clear()
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        if not username:
            return apology("must provide username", 404)
        elif not "password":
            return apology("must provide password", 404)
        elif not confirmation:
            return apology("must provide confirmation password", 404)

        rows = db.execute('SELECT * FROM users WHERE username = :username', username=username)
        if len(rows):
            return apology("username already taken", 404)
        elif password != confirmation:
            return apology("passwords did not match", 404)

        db.execute('INSERT INTO users (username, hash) VALUES (:username, :hash)',
                   username=username, hash=generate_password_hash(password))

        user_id = db.execute("SELECT id FROM users WHERE username = :username", username=username)[0]["id"]
        session["user_id"] = user_id

        flash("Registered!")
        return redirect("/")
    else:
        return render_template("register.html")


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    """Sell shares of stock"""

    user_id = session["user_id"]

    if request.method == "POST":
        symbol = request.form.get("symbol").upper()
        shares = request.form.get("shares")
        shares = int(shares) if shares else 0
        if not symbol:
            return apology("missing symbol", 404)
        if shares <= 0:
            return apology("missing shares", 404)

        data = db.execute(
            'SELECT * FROM stocks WHERE user_id=:user_id AND symbol=:symbol', user_id=user_id, symbol=symbol
        )
        if not data:
            return apology("symbol not valid", 404)

        data = data[0]
        price = lookup(symbol)["price"]
        current = data["shares"]
        company = data["company"]

        if shares > current:
            db.execute(
                'INSERT INTO transactions (user_id, flag, symbol, shares, price, company, date_time)'
                'VALUES (:user_id, :flag, :symbol, :shares, :price, :company, datetime("now", "localtime"))',
                user_id=user_id, flag=0, symbol=symbol, shares=shares, price=price, company=company
            )
            return apology("not enough shares", 404)

        income = round(shares * price, 2)
        cash = round(db.execute('SELECT cash FROM users WHERE id=:id', id=user_id)[0]["cash"] + income, 2)
        db.execute('UPDATE users SET cash=:cash WHERE id=:id', cash=cash, id=user_id)

        db.execute(
            'INSERT INTO transactions (user_id, flag, symbol, shares, price, company, date_time)'
            'VALUES (:user_id, :flag, :symbol, :shares, :price, :company, datetime("now", "localtime"))',
            user_id=user_id, flag=-1, symbol=symbol, shares=shares, price=price, company=company
        )

        if shares == current:
            db.execute('DELETE FROM stocks WHERE id=:id', id=data["id"])
        else:
            db.execute('UPDATE stocks SET shares=:shares WHERE id=:id', shares=(data["shares"] - shares), id=data["id"])

        flash(f'Congratulations! You sold {shares} {symbol} stock(s) for ${income}!')
        return redirect("/")
    else:
        options = db.execute('SELECT * FROM stocks WHERE user_id=:user_id', user_id=user_id)
        return render_template("sell.html", options=options)


@app.errorhandler(Exception)
def errorhandler(e):
    """Handle error"""
    if not isinstance(e, HTTPException):
        e = InternalServerError()
    return apology(e.name, e.code)


for code in default_exceptions:
    app.errorhandler(code)(errorhandler)

import logging
import os
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_babel import Babel, gettext as _
from ompass.exceptions import OmpassApiException
from werkzeug.security import check_password_hash, generate_password_hash

from models import User, db
from ompass_service import OmpassService

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "demo-secret-key-change-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///demomail.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["BABEL_DEFAULT_LOCALE"] = "en"
app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"

db.init_app(app)

OMPASS_CLIENT_ID = os.environ.get("OMPASS_CLIENT_ID")
OMPASS_SECRET_KEY = os.environ.get("OMPASS_SECRET_KEY")
OMPASS_BASE_URL = os.environ.get("OMPASS_BASE_URL", "https://api.ompasscloud.com")

if not OMPASS_CLIENT_ID or not OMPASS_SECRET_KEY:
    raise RuntimeError(
        "OMPASS_CLIENT_ID and OMPASS_SECRET_KEY must be set as environment variables. "
        "See README.md for configuration details."
    )

ompass_service = OmpassService(OMPASS_CLIENT_ID, OMPASS_SECRET_KEY, OMPASS_BASE_URL)


def get_locale():
    lang = request.args.get("lang")
    if lang and lang in ("en", "ko", "ja"):
        session["lang"] = lang
    return session.get("lang", "en")


babel = Babel(app, locale_selector=get_locale)


@app.context_processor
def inject_locale():
    return {"get_locale": get_locale}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    user_id = session.get("user_id")
    if user_id:
        return db.session.get(User, user_id)
    return None


# ── Page Routes ──

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/register")
def register_form():
    return render_template("register.html")


@app.route("/home")
@login_required
def home():
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for("login"))
    return render_template("home.html", user=user)


@app.route("/settings")
@login_required
def settings():
    user = get_current_user()
    if not user:
        session.clear()
        return redirect(url_for("login"))
    return render_template("settings.html", user=user)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ── Auth Routes ──

@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    name = request.form.get("name", "").strip()
    password = request.form.get("password", "")

    if User.query.filter_by(username=username).first():
        flash(_("Username already exists"), "error")
        return redirect(url_for("register_form"))
    if User.query.filter_by(email=email).first():
        flash(_("Email already exists"), "error")
        return redirect(url_for("register_form"))

    user = User(
        username=username,
        email=email,
        name=name,
        password=generate_password_hash(password),
    )
    db.session.add(user)
    db.session.commit()

    flash(_("Registration complete. Please log in."), "success")
    return redirect(url_for("login"))


@app.route("/auth/check-user", methods=["POST"])
def check_user():
    username = request.form.get("username", "").strip()
    has_auth = ompass_service.has_authenticators(username)
    if not has_auth:
        return jsonify({"ompassRegistered": False})
    user = User.query.filter_by(username=username).first()
    passwordless = user.passwordless_enabled if user else False
    return jsonify({"ompassRegistered": passwordless})


@app.route("/auth/login", methods=["POST"])
def auth_login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password, password):
        flash(_("Invalid username or password."), "error")
        return redirect(url_for("login"))

    if user.ompass_registered:
        try:
            response = ompass_service.start_auth(username)
            session["auth_username"] = username
            session["ompass_registered"] = True
            session["password_verified"] = True
            return render_template(
                "ompass-auth.html",
                username=username,
                ompass_url=response.ompass_url,
                is_registered=response.registered_ompass,
            )
        except OmpassApiException as e:
            logger.error("OMPASS auth start failed: %s", e)
            flash(f"2FA failed: {e}", "error")
            return redirect(url_for("login"))
    else:
        user.last_login_at = datetime.now()
        db.session.commit()
        session["user_id"] = user.id
        return redirect(url_for("home"))


@app.route("/auth/start", methods=["POST"])
def auth_start():
    username = request.form.get("username", "").strip()
    logger.info("[/auth/start] Starting auth for username: %s", username)

    has_auth = ompass_service.has_authenticators(username)
    logger.info("[/auth/start] hasAuthenticators result: %s", has_auth)

    if not has_auth:
        logger.warning("[/auth/start] No authenticators found for user: %s", username)
        return jsonify({"success": False, "error": _("Cannot proceed with authentication.")})

    try:
        logger.info("[/auth/start] Calling ompass_service.start_auth()")
        response = ompass_service.start_auth(username)
        logger.info("[/auth/start] startAuth response - ompassUrl: %s", response.ompass_url)

        session["auth_username"] = username
        session["ompass_registered"] = True

        return jsonify({"success": True, "ompassUrl": response.ompass_url})
    except OmpassApiException as e:
        logger.error("[/auth/start] OMPASS auth start failed: %s", e)
        return jsonify({"success": False, "error": _("Cannot proceed with authentication.")})


@app.route("/auth/register-ompass", methods=["POST"])
def register_ompass():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": _("Please log in first.")})

    try:
        response = ompass_service.start_auth(user.username)
        session["auth_username"] = user.username
        session["ompass_registered"] = False
        session["registering_ompass"] = True
        return jsonify({"success": True, "ompassUrl": response.ompass_url})
    except OmpassApiException as e:
        logger.error("OMPASS registration start failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route("/auth/toggle-passwordless", methods=["POST"])
def toggle_passwordless():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": _("Please log in first.")})
    if not user.ompass_registered:
        return jsonify({"success": False, "error": _("OMPASS is not registered.")})

    enabled = request.form.get("enabled", "false").lower() == "true"
    user.passwordless_enabled = enabled
    db.session.commit()
    return jsonify({"success": True, "passwordlessEnabled": enabled})


@app.route("/auth/delete-ompass", methods=["POST"])
def delete_ompass():
    user = get_current_user()
    if not user:
        return jsonify({"success": False, "error": _("Please log in first.")})

    try:
        deleted = ompass_service.delete_all_authenticators(user.username)
        if deleted > 0:
            user.ompass_registered = False
            db.session.commit()
        return jsonify({"success": True, "deleted": deleted})
    except OmpassApiException as e:
        logger.error("OMPASS delete failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route("/auth/callback")
def auth_callback():
    token = request.args.get("token", "")
    username = session.get("auth_username")

    if not username:
        return render_template("auth-callback.html", success=False, error="Session expired. Please try again.")

    try:
        response = ompass_service.verify_token(username, token)

        if response.is_verified:
            user = User.query.filter_by(username=username).first()
            if user:
                user.ompass_registered = True
                user.last_login_at = datetime.now()
                db.session.commit()

                session["user_id"] = user.id
                session.pop("auth_username", None)
                session.pop("ompass_registered", None)

                return render_template("auth-callback.html", success=True)

        return render_template("auth-callback.html", success=False, error="Token verification failed.")
    except OmpassApiException as e:
        logger.error("OMPASS token verification failed: %s", e)
        return render_template("auth-callback.html", success=False, error=f"Verification failed: {e}")


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8443, ssl_context="adhoc", debug=True)

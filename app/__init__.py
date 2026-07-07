from flask import Flask
from .config import load_configurations, configure_logging
from .views import webhook_blueprint

def create_app():
    app = Flask(__name__)

    # Load configurations and logging settings
    load_configurations(app)
    configure_logging()

    # Ensure the SQLite schema exists before serving any requests.
    from .utils.db_utils import init_db
    with app.app_context():
        init_db()

    # Register the webhook blueprint
    app.register_blueprint(webhook_blueprint)

    return app

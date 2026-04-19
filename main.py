"""Root entry point for the M&S Multi-Faktor-Modell Dash app."""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.main import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=False, host="0.0.0.0", port=5000)

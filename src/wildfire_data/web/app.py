"""Legacy uvicorn entry; desktop imports the side-effect-free factory directly."""
from wildfire_data.web.application import STATIC, create_app

app = create_app()

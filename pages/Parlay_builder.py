"""The parlay builder on karma's phone: <the app's address>/Parlay_builder

Unlisted: app.py hides Streamlit's page list, so this page is reached only by its address. It runs the
Journal's builder page (tools/pages/1_Parlay_builder.py) against data/parlay_feed.json.gz, the file
`bash tools/publish_builder.sh` writes and pushes from the Mac. No Mac, no journal/ folder needed.
"""
import os
import runpy

os.environ["BW_BUILDER_FEED"] = "1"
runpy.run_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "pages", "1_Parlay_builder.py"), run_name="__main__")

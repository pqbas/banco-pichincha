import json
from pathlib import Path

import aws_cdk as cdk

from stack import ServicioStack

config = json.loads((Path(__file__).resolve().parent / "config.json").read_text())
app = cdk.App()
ServicioStack(app, config["stack"], config=config, env=cdk.Environment(region=config.get("region", "us-east-1")))
app.synth()

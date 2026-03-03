#!/usr/bin/env python3
import os

import aws_cdk as cdk

from stacks.infra_stack import InfraStack


app = cdk.App()
InfraStack(app, "InfraStack")

app.synth()

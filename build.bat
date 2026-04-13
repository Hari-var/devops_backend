#!/usr/bin/env bash

curl -fsSL https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip -o terraform.zip
python -m zipfile -e terraform.zip .
chmod +x terraform
export PATH="$PATH:$(pwd)"
terraform -version

pip install -r requirements.txt
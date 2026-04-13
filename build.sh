#!/usr/bin/env bash

curl -fsSL https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip -o terraform.zip
python -m zipfile -e terraform.zip .
chmod +x terraform

# Move to project folder (persistent)
mkdir -p ./bin
mv terraform ./bin/

# Add to PATH permanently
echo 'export PATH="$PATH:$(pwd)/bin"' >> ~/.bashrc

terraform_path="$(pwd)/bin/terraform"
$terraform_path -version

pip install -r requirements.txt
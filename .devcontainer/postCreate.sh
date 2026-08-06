#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update -qq

# Instalar GitHub CLI (gh) desde el repositorio oficial para tener la versión más reciente
(type -p wget >/dev/null || (sudo apt update && sudo apt install wget -y)) \
	&& sudo mkdir -p -m 755 /etc/apt/keyrings \
	&& out=$(mktemp) && wget -nv -O"$out" https://cli.github.com/packages/githubcli-archive-keyring.gpg \
	&& cat "$out" | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
	&& sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
	&& sudo mkdir -p -m 755 /etc/apt/sources.list.d \
	&& echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
	&& sudo apt update \
	&& sudo apt install gh -y

sudo apt-get install -y -qq libgl1 libglib2.0-0
python -m pip install -e 'packages/dof2md[test]' 
python -m pip install -e 'packages/dofjson[test]' 
python -m pip install -e 'packages/nota2md[test]' 
python -m pip install -r requirements.txt
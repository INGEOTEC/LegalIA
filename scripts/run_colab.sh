#!/bin/bash

colab new -s legalia --gpu T4 
colab install -s legalia "nota2md[ocr]"
colab drivemount -s legalia
colab exec -s legalia -f fetch_legal_provisions_provenance.py --timeout 3600
colab stop -s legalia
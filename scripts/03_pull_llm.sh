#!/usr/bin/env bash
# Récupère le LLM local via Ollama.
# Prérequis : Ollama installé (https://ollama.com/download).
set -euo pipefail

MODEL="${LLM_MODEL:-qwen2.5-coder:7b}"
echo "Pull du modèle $MODEL via Ollama..."
ollama pull "$MODEL"
echo "OK. Modèles disponibles :"
ollama list

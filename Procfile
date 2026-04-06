web: gunicorn --workers 2 --threads 8 --worker-class gthread --timeout 120 --keep-alive 5 --bind 0.0.0.0:$PORT mcp_server.server:app

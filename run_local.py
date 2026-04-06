import os
from mcp_server.server import app
from shared.logger import get_logger

logger = get_logger("mcp_server")

if __name__ == "__main__":
    # Gunakan port 8080 sebagai default, atau ambil dari .env
    port = int(os.environ.get("MCP_SERVER_PORT", 8080))
    
    logger.info(f"[Local] Menjalankan server MCP lokal di port {port}...")
    
    # Jalankan Flask app
    app.run(host="0.0.0.0", port=port, debug=True)

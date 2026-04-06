import os
import sys

from shared.logger import get_logger
from agents.bang_jek.agent import BangJekOrchestrator

# Import sub-agents (bungkus pake try-except biar gak crash kalau belum selesai dikoding)
try:
    from agents.the_planner.agent import ThePlannerAgent
    planner_available = True
except ImportError:
    planner_available = False

try:
    from agents.the_archivist.agent import TheArchivistAgent
    archivist_available = True
except ImportError:
    archivist_available = False

try:
    from agents.the_auditor.agent import TheAuditorAgent
    auditor_available = True
except ImportError:
    auditor_available = False


logger = get_logger("chat_cli")

def main():
    print("==================================================")
    print("🛵 OJOLBOOST MAMS - Bang Jek Interactive Terminal")
    print("==================================================")
    print("Memuat sistem dan menginisialisasi AI...")

    # 1. Inisialisasi Orchestrator (Bang Jek)
    orchestrator = BangJekOrchestrator()
    orchestrator.initialize()

    # 2. Register Sub-Agents yang udah tersedia
    registered_agents = []
    if planner_available:
        try:
            orchestrator.register_sub_agent("The Planner", ThePlannerAgent())
            registered_agents.append("The Planner")
        except Exception as e:
            print(f"⚠️ Gagal memuat The Planner: {e}")
            
    if archivist_available:
        try:
            orchestrator.register_sub_agent("The Archivist", TheArchivistAgent())
            registered_agents.append("The Archivist")
        except Exception as e:
            print(f"⚠️ Gagal memuat The Archivist: {e}")
            
    if auditor_available:
        try:
            orchestrator.register_sub_agent("The Auditor", TheAuditorAgent())
            registered_agents.append("The Auditor")
        except Exception as e:
            print(f"⚠️ Gagal memuat The Auditor: {e}")

    if not registered_agents:
        print("⚠️ Perhatian: Belum ada sub-agen yang ter-register!")
    else:
        print(f"✔️ Sub-agen aktif: {', '.join(registered_agents)}")

    print("\nBang Jek udah standby! (Ketik 'exit' buat udahan)")
    print("--------------------------------------------------\n")

    # 3. Main Chat Loop
    while True:
        try:
            user_input = input("Lo       : ")
            
            # Cek perintah exit
            if user_input.strip().lower() in ["exit", "quit", "keluar"]:
                print("Bang Jek : Siap Bang, hati-hati di jalan ya! Salam OjolBoost.")
                break
                
            if not user_input.strip():
                continue

            # Proses lewat orchestrator
            response = orchestrator.process(user_input, driver_id="DRIVER_TEST_01")
            
            # Output ke terminal
            print(f"Bang Jek : {response.narration}\n")
            
        except KeyboardInterrupt:
            print("\nBang Jek : Siap Bang, hati-hati di jalan ya!")
            break
        except Exception as e:
            print(f"\n⚠️ Error internal Bange Jek: {e}\n")


if __name__ == "__main__":
    main()

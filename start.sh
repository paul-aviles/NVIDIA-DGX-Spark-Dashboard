
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

bash
export WEBTOP_HIDE_INTERFACES="enp1s0f0np0,enp1s0f1np1,enP2p1s0f0np0,enP2p1s0f1np1"
uvicorn server:app --host 0.0.0.0 --port 8000
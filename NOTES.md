cd /home/brendan/computer/cptr/frontend
npm install
npm run build


cd /home/brendan/computer
. .venv/bin/activate
python -m cptr.cli run --host 0.0.0.0 --port 4200 --headless
# ChillPay Assistant

Assistant system using OpenAI API + ChillPay API, with Google Sheet based instruction config.

## Project Structure


## Setup

1. Clone or upload the project to your server.
2. Create a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate

วิธีการรันบน Terminal
1. เรียกใช้ครั้งแรกในเทอร์มินัล (interactive mode)
cd /home/ubuntu/xpay
source venv/bin/activate
cd app
python main.py
2. จากนั้นค่อย start ผ่าน pm2
pm2 start main.py --name main --interpreter python3
-------- ถ้าไม่ได้ ------------
✅ ทางเลือกที่ 1: รีสตาร์ทโปรเซสเดิม
pm2 restart main
🔄 ทางเลือกที่ 2: ลบแล้วรันใหม่
pm2 delete main
pm2 start main.py --name main --interpreter python3
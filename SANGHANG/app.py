import os
import io
import time
import zipfile
import pandas as pd
from flask import Flask, request, redirect, render_template, url_for, send_file, jsonify
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename

app = Flask(__name__)

# --- CẤU HÌNH MONGODB ATLAS ---
# Lưu ý: Đã thêm /sanghang_db vào sau địa chỉ để chỉ định database
app.config["MONGO_URI"] = "mongodb+srv://toiyeucf1_db_user:jRxXWUFs9dnzZXYJ@cluster0.bmsszvn.mongodb.net/sanghang_db?appName=Cluster0"

# Thêm try-except để bắt lỗi kết nối ngay lúc khởi động
try:
    mongo = PyMongo(app)
    db = mongo.db
    # Thử lệnh nhẹ để kiểm tra kết nối
    mongo.cx.server_info()
    print("✅ Đã kết nối thành công tới MongoDB Atlas!")
except Exception as e:
    print("❌ LỖI KẾT NỐI MONGO ATLAS:", e)
    print("👉 Hãy kiểm tra lại Network Access (Whitelist IP) trên trang quản trị Atlas.")

# Cấu hình nơi lưu ảnh
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- ROUTES ---

@app.route('/')
def home():
    try:
        # Lấy danh sách sessions, sắp xếp ngày mới nhất lên đầu (-1)
        sessions = list(db.sessions.find().sort("work_date", -1))
        
        # Tính toán số lượng cặp cont cho mỗi session
        for s in sessions:
            s['pair_count'] = db.pairs.count_documents({'session_id': s['_id']})
            
        return render_template('home.html', sessions=sessions)
    except Exception as e:
        return f"Lỗi truy vấn Database: {e}. <br>Vui lòng kiểm tra lại kết nối internet hoặc Whitelist IP."

@app.route('/create_session', methods=['POST'])
def create_session():
    date_str = request.form.get('work_date')
    shift_val = request.form.get('shift')
    worker_val = request.form.get('worker_count')
    names_list = request.form.getlist('worker_name') 
    name_str = ", ".join(names_list)
    
    if date_str:
        new_session = {
            'work_date': datetime.strptime(date_str, '%Y-%m-%d'),
            'shift': shift_val,
            'worker_count': int(worker_val) if worker_val else 0,
            'worker_name': name_str,
            'created_at': datetime.now()
        }
        result = db.sessions.insert_one(new_session)
        return redirect(url_for('dashboard', session_id=str(result.inserted_id)))
        
    return redirect(url_for('home'))

@app.route('/delete_session/<session_id>')
def delete_session(session_id):
    try:
        s_id = ObjectId(session_id)
        # 1. Xóa ảnh vật lý
        pairs = db.pairs.find({'session_id': s_id})
        for pair in pairs:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    if os.path.exists(file_path): os.remove(file_path)
        
        # 2. Xóa dữ liệu DB
        db.pairs.delete_many({'session_id': s_id})
        db.sessions.delete_one({'_id': s_id})
    except Exception as e:
        print(f"Lỗi khi xóa: {e}")
    
    return redirect(url_for('home'))

@app.route('/dashboard/<session_id>', methods=['GET', 'POST'])
def dashboard(session_id):
    try:
        s_id = ObjectId(session_id)
        
        if request.method == 'POST':
            source = request.form.get('source_cont')
            target = request.form.get('target_cont')
            if source and target:
                new_pair = {
                    'session_id': s_id,
                    'source_cont': source,
                    'target_cont': target,
                    'photos': [] 
                }
                db.pairs.insert_one(new_pair)
            return redirect(url_for('dashboard', session_id=session_id))
        
        session_data = db.sessions.find_one_or_404({'_id': s_id})
        pairs = list(db.pairs.find({'session_id': s_id}))
        session_data['pairs'] = pairs
        
        return render_template('dashboard.html', session=session_data)
    except Exception as e:
        return f"Lỗi Dashboard: {e}"

# API Kiểm tra trùng lặp
@app.route('/check_duplicate/<session_id>', methods=['POST'])
def check_duplicate(session_id):
    data = request.get_json()
    source_cont = data.get('source_cont')
    
    existing = db.pairs.find_one({
        'session_id': ObjectId(session_id), 
        'source_cont': source_cont
    })
    
    return jsonify({'exists': True if existing else False})

@app.route('/delete_pair/<pair_id>')
def delete_pair(pair_id):
    try:
        p_id = ObjectId(pair_id)
        pair = db.pairs.find_one({'_id': p_id})
        
        if pair:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    if os.path.exists(file_path): os.remove(file_path)
            
            db.pairs.delete_one({'_id': p_id})
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except:
        pass
    return redirect(url_for('home'))

@app.route('/upload_image/<pair_id>', methods=['POST'])
def upload_image(pair_id):
    try:
        p_id = ObjectId(pair_id)
        pair = db.pairs.find_one({'_id': p_id})
        
        if not pair or 'photo' not in request.files:
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
            
        file = request.files['photo']
        if file.filename == '':
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))

        if file:
            timestamp = int(time.time())
            filename = secure_filename(f"{pair_id}_{timestamp}_{file.filename}")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            
            db.pairs.update_one({'_id': p_id}, {'$push': {'photos': filename}})

        return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e:
        return f"Lỗi upload: {e}"

@app.route('/delete_image/<pair_id>/<filename>')
def delete_image(pair_id, filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path): os.remove(file_path)
    
    db.pairs.update_one({'_id': ObjectId(pair_id)}, {'$pull': {'photos': filename}})
    
    pair = db.pairs.find_one({'_id': ObjectId(pair_id)})
    return redirect(url_for('dashboard', session_id=str(pair['session_id'])))

# --- XUẤT FILE ---
@app.route('/export_excel/<session_id>')
def export_excel(session_id):
    s_id = ObjectId(session_id)
    session_data = db.sessions.find_one_or_404({'_id': s_id})
    pairs = list(db.pairs.find({'session_id': s_id}))
    
    data_list = []
    for index, pair in enumerate(pairs, start=1):
        photos = pair.get('photos', [])
        photo_links = [url_for('static', filename='uploads/' + p, _external=True) for p in photos]
        
        data_list.append({
            'STT': index,
            'Ngày': session_data['work_date'].strftime('%d-%m-%Y'),
            'Ca': session_data['shift'],
            'Người phụ trách': session_data['worker_name'],
            'Số lượng nhân sự': session_data['worker_count'],
            'Cont Rút': pair['source_cont'],
            'Cont Đóng': pair['target_cont'],
            'Link ảnh': "\n".join(photo_links)
        })

    df = pd.DataFrame(data_list)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    output.seek(0)
    filename = f"SangHang_{session_data['work_date'].strftime('%d-%m-%Y')}.xlsx"
    return send_file(output, download_name=filename, as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/download_images/<session_id>')
def download_images(session_id):
    s_id = ObjectId(session_id)
    session_data = db.sessions.find_one_or_404({'_id': s_id})
    pairs = db.pairs.find({'session_id': s_id})
    
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        for pair in pairs:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    if os.path.exists(file_path):
                        archive_name = f"{pair['source_cont']}_{pair['target_cont']}/{filename}"
                        zf.write(file_path, archive_name)
    memory_file.seek(0)
    return send_file(memory_file, download_name=f"All_Images_{session_data['work_date'].strftime('%d-%m-%Y')}.zip", as_attachment=True)

if __name__ == '__main__':
    # Chạy host 0.0.0.0 để điện thoại vào được
    app.run(host='0.0.0.0', debug=True)
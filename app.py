import os
import io
import time
import zipfile
import pandas as pd
from flask import Flask, request, redirect, render_template, url_for, send_file, jsonify, make_response
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename
from gridfs import GridFS               # <-- Mới: Thư viện lưu file vào Mongo
from PIL import Image                   # <-- Mới: Thư viện nén ảnh

app = Flask(__name__)

# --- CẤU HÌNH MONGODB ATLAS ---
app.config["MONGO_URI"] = "mongodb+srv://toiyeucf1_db_user:jRxXWUFs9dnzZXYJ@cluster0.bmsszvn.mongodb.net/sanghang_db?appName=Cluster0"

try:
    mongo = PyMongo(app)
    db = mongo.db
    fs = GridFS(db)  # <-- Mới: Khởi tạo hệ thống file GridFS
    mongo.cx.server_info()
    print("✅ Đã kết nối thành công tới MongoDB Atlas!")
except Exception as e:
    print("❌ LỖI KẾT NỐI MONGO ATLAS:", e)

# (Không cần UPLOAD_FOLDER nữa vì lưu vào DB rồi)

# --- ROUTES ---

@app.route('/')
def home():
    try:
        sessions = list(db.sessions.find().sort("work_date", -1))
        for s in sessions:
            s['pair_count'] = db.pairs.count_documents({'session_id': s['_id']})
        return render_template('home.html', sessions=sessions)
    except Exception as e:
        return f"Lỗi truy vấn: {e}"

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
        # 1. Xóa ảnh trong GridFS
        pairs = db.pairs.find({'session_id': s_id})
        for pair in pairs:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_doc = db.fs.files.find_one({"filename": filename})
                    if file_doc: fs.delete(file_doc['_id'])
        
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

@app.route('/check_duplicate/<session_id>', methods=['POST'])
def check_duplicate(session_id):
    data = request.get_json()
    source_cont = data.get('source_cont')
    existing = db.pairs.find_one({'session_id': ObjectId(session_id), 'source_cont': source_cont})
    return jsonify({'exists': True if existing else False})

@app.route('/delete_pair/<pair_id>')
def delete_pair(pair_id):
    try:
        p_id = ObjectId(pair_id)
        pair = db.pairs.find_one({'_id': p_id})
        if pair:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_doc = db.fs.files.find_one({"filename": filename})
                    if file_doc: fs.delete(file_doc['_id'])
            db.pairs.delete_one({'_id': p_id})
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except:
        pass
    return redirect(url_for('home'))

# --- MỚI: Route để hiển thị ảnh từ Database ---
@app.route('/image/<filename>')
def get_image(filename):
    try:
        # Tìm file trong GridFS
        file = fs.find_one({"filename": filename})
        if not file:
            return "Image not found", 404
        
        # Trả về dữ liệu ảnh
        response = make_response(file.read())
        response.headers['Content-Type'] = 'image/jpeg'
        # Cache ảnh 30 ngày để load nhanh hơn
        response.headers['Cache-Control'] = 'public, max-age=2592000'
        return response
    except Exception as e:
        return str(e)

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

            # --- NÉN ẢNH TRƯỚC KHI LƯU (Quan trọng) ---
            img = Image.open(file)
            # Convert sang RGB nếu là ảnh PNG/RGBA để lưu JPG
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            
            # Resize: Giới hạn chiều lớn nhất là 1024px (đủ nét xem điện thoại)
            img.thumbnail((1024, 1024))
            
            # Lưu vào bộ nhớ đệm
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=70) # Nén chất lượng 70%
            img_byte_arr.seek(0)
            
            # Lưu vào GridFS
            fs.put(img_byte_arr, filename=filename, content_type='image/jpeg')
            
            # Cập nhật tên ảnh vào danh sách
            db.pairs.update_one({'_id': p_id}, {'$push': {'photos': filename}})

        return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e:
        return f"Lỗi upload: {e}"

@app.route('/delete_image/<pair_id>/<filename>')
def delete_image(pair_id, filename):
    try:
        # Xóa file trong GridFS
        file_doc = db.fs.files.find_one({"filename": filename})
        if file_doc:
            fs.delete(file_doc['_id'])
        
        # Xóa tên file trong danh sách pairs
        db.pairs.update_one({'_id': ObjectId(pair_id)}, {'$pull': {'photos': filename}})
        
        pair = db.pairs.find_one({'_id': ObjectId(pair_id)})
        return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e:
        return f"Lỗi xóa ảnh: {e}"

@app.route('/export_excel/<session_id>')
def export_excel(session_id):
    s_id = ObjectId(session_id)
    session_data = db.sessions.find_one_or_404({'_id': s_id})
    pairs = list(db.pairs.find({'session_id': s_id}))
    
    data_list = []
    for index, pair in enumerate(pairs, start=1):
        photos = pair.get('photos', [])
        # Sửa đường dẫn link ảnh trong Excel
        photo_links = [url_for('get_image', filename=p, _external=True) for p in photos]
        
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
                    # Lấy file từ GridFS
                    file_doc = fs.find_one({"filename": filename})
                    if file_doc:
                        archive_name = f"{pair['source_cont']}_{pair['target_cont']}/{filename}"
                        zf.writestr(archive_name, file_doc.read())
    memory_file.seek(0)
    return send_file(memory_file, download_name=f"All_Images_{session_data['work_date'].strftime('%d-%m-%Y')}.zip", as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)

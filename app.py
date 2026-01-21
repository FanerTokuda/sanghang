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
from gridfs import GridFS
from PIL import Image

app = Flask(__name__)

# --- CẤU HÌNH MONGODB ATLAS ---
app.config["MONGO_URI"] = "mongodb+srv://toiyeucf1_db_user:jRxXWUFs9dnzZXYJ@cluster0.bmsszvn.mongodb.net/sanghang_db?appName=Cluster0"

try:
    mongo = PyMongo(app)
    db = mongo.db
    fs = GridFS(db)
    mongo.cx.server_info()
    print("✅ Đã kết nối thành công tới MongoDB Atlas!")
except Exception as e:
    print("❌ LỖI KẾT NỐI MONGO ATLAS:", e)

# --- ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def home():
    try:
        # --- 1. LOGIC THỐNG KÊ THÁNG (MỚI) ---
        now = datetime.now()
        # Lấy ngày đầu tháng hiện tại (ví dụ: 01/02/2026)
        start_of_month = datetime(now.year, now.month, 1)
        
        # Lấy tất cả các ngày làm việc trong tháng này
        month_sessions = list(db.sessions.find({'work_date': {'$gte': start_of_month}}))
        
        worker_stats = {} # Lưu kết quả: {'Toàn': 5, 'Tuấn': 3 ...}
        session_ids = []
        
        for s in month_sessions:
            session_ids.append(s['_id'])
            # Tách tên người làm (VD: "Toàn, Tuấn" -> ["Toàn", "Tuấn"]) và đếm
            if s.get('worker_name'):
                names = [n.strip() for n in s['worker_name'].split(',')]
                for name in names:
                    if name:
                        worker_stats[name] = worker_stats.get(name, 0) + 1
        
        # Tính tổng số Cont chỉ trong tháng này
        month_total_pairs = 0
        if session_ids:
            month_total_pairs = db.pairs.count_documents({'session_id': {'$in': session_ids}})
            
        stats = {
            'month': now.strftime('%m/%Y'),
            'worker_stats': worker_stats,
            'month_total_pairs': month_total_pairs
        }

        # --- 2. LOGIC TÌM KIẾM ---
        search_query = request.args.get('q', '').strip()
        search_results = []
        
        if search_query:
            pairs = list(db.pairs.find({
                '$or': [
                    {'source_cont': {'$regex': search_query, '$options': 'i'}},
                    {'target_cont': {'$regex': search_query, '$options': 'i'}}
                ]
            }))
            
            for p in pairs:
                s = db.sessions.find_one({'_id': p['session_id']})
                if s:
                    p['work_date'] = s['work_date']
                    p['shift'] = s['shift']
                    search_results.append(p)
        
        # --- 3. LẤY DANH SÁCH LỊCH SỬ ---
        sessions = list(db.sessions.find().sort("work_date", -1))
        for s in sessions:
            s['pair_count'] = db.pairs.count_documents({'session_id': s['_id']})
            
        return render_template('home.html', sessions=sessions, stats=stats, search_results=search_results, search_query=search_query)
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
                    # Sửa lỗi truy cập collection
                    file_doc = db['fs.files'].find_one({"filename": filename})
                    if file_doc: fs.delete(file_doc['_id'])
        
        # 2. Xóa dữ liệu DB
        db.pairs.delete_many({'session_id': s_id})
        db.sessions.delete_one({'_id': s_id})
    except Exception as e:
        print(f"Lỗi khi xóa session: {e}")
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

@app.route('/update_pair/<pair_id>', methods=['POST'])
def update_pair(pair_id):
    try:
        p_id = ObjectId(pair_id)
        new_source = request.form.get('edit_source_cont')
        new_target = request.form.get('edit_target_cont')
        
        pair = db.pairs.find_one({'_id': p_id})
        if pair and new_source and new_target:
            db.pairs.update_one(
                {'_id': p_id}, 
                {'$set': {'source_cont': new_source, 'target_cont': new_target}}
            )
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e:
        print(f"Lỗi update: {e}")
    return redirect(url_for('home'))

@app.route('/delete_pair/<pair_id>')
def delete_pair(pair_id):
    try:
        p_id = ObjectId(pair_id)
        pair = db.pairs.find_one({'_id': p_id})
        if pair:
            if 'photos' in pair:
                for filename in pair['photos']:
                    # Sửa lỗi truy cập collection
                    file_doc = db['fs.files'].find_one({"filename": filename})
                    if file_doc: fs.delete(file_doc['_id'])
            db.pairs.delete_one({'_id': p_id})
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e:
        print(f"Lỗi xóa pair: {e}")
    return redirect(url_for('home'))

@app.route('/image/<filename>')
def get_image(filename):
    try:
        file = fs.find_one({"filename": filename})
        if not file:
            return "Image not found", 404
        
        response = make_response(file.read())
        response.headers['Content-Type'] = 'image/jpeg'
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

            img = Image.open(file)
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img.thumbnail((1024, 1024))
            
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=70)
            img_byte_arr.seek(0)
            
            fs.put(img_byte_arr, filename=filename, content_type='image/jpeg')
            
            db.pairs.update_one({'_id': p_id}, {'$push': {'photos': filename}})

        return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e:
        return f"Lỗi upload: {e}"

@app.route('/delete_image/<pair_id>/<filename>')
def delete_image(pair_id, filename):
    try:
        # Sửa lỗi truy cập collection
        file_doc = db['fs.files'].find_one({"filename": filename})
        if file_doc:
            fs.delete(file_doc['_id'])
        
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
                    file_doc = fs.find_one({"filename": filename})
                    if file_doc:
                        archive_name = f"{pair['source_cont']}_{pair['target_cont']}/{filename}"
                        zf.writestr(archive_name, file_doc.read())
    memory_file.seek(0)
    return send_file(memory_file, download_name=f"All_Images_{session_data['work_date'].strftime('%d-%m-%Y')}.zip", as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True)

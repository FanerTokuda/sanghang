import os
import io
import time
import zipfile
import pandas as pd
from flask import Flask, request, redirect, render_template, url_for, send_file, jsonify, make_response, flash
from flask_pymongo import PyMongo
from bson.objectid import ObjectId
from datetime import datetime
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from gridfs import GridFS
from PIL import Image
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

app = Flask(__name__)

# --- CẤU HÌNH BẢO MẬT & DB ---
app.secret_key = 'chia_khoa_bao_mat_cua_sang_hang_2026' # Bắt buộc để dùng session
app.config["MONGO_URI"] = "mongodb+srv://toiyeucf1_db_user:jRxXWUFs9dnzZXYJ@cluster0.bmsszvn.mongodb.net/sanghang_db?appName=Cluster0"

try:
    mongo = PyMongo(app)
    db = mongo.db
    fs = GridFS(db)
    print("✅ Đã kết nối MongoDB Atlas!")
except Exception as e:
    print("❌ Lỗi kết nối:", e)

# --- CẤU HÌNH FLASK-LOGIN ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Chưa đăng nhập sẽ bị đá về đây

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.role = user_data.get('role', 'user') # Mặc định là user

@login_manager.user_loader
def load_user(user_id):
    u = db.users.find_one({"_id": ObjectId(user_id)})
    if u: return User(u)
    return None

# --- ROUTE KHỞI TẠO TÀI KHOẢN (CHẠY 1 LẦN) ---
@app.route('/init_accounts')
def init_accounts():
    # Tạo Admin (Pass: admin123)
    if not db.users.find_one({'username': 'admin'}):
        db.users.insert_one({'username': 'admin', 'password': generate_password_hash('admin123'), 'role': 'admin'})
    
    # Tạo Nhân viên (Pass: 123456)
    if not db.users.find_one({'username': 'staff'}):
        db.users.insert_one({'username': 'staff', 'password': generate_password_hash('123456'), 'role': 'user'})
    
    return "Đã tạo xong: Admin (pass: admin123) và Staff (pass: 123456)"

# --- ROUTES ĐĂNG NHẬP/XUẤT ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user_data = db.users.find_one({'username': username})
        
        if user_data and check_password_hash(user_data['password'], password):
            user = User(user_data)
            login_user(user)
            return redirect(url_for('home'))
        else:
            return render_template('login.html', error="Sai tài khoản hoặc mật khẩu!")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# --- ROUTES CHÍNH (ĐÃ BẢO VỆ) ---

@app.route('/', methods=['GET', 'POST'])
@login_required
def home():
    try:
        # Thống kê tháng
        now = datetime.now()
        start_of_month = datetime(now.year, now.month, 1)
        month_sessions = list(db.sessions.find({'work_date': {'$gte': start_of_month}}))
        
        worker_stats = {}
        session_ids = []
        for s in month_sessions:
            session_ids.append(s['_id'])
            if s.get('worker_name'):
                names = [n.strip() for n in s['worker_name'].split(',')]
                for name in names:
                    if name: worker_stats[name] = worker_stats.get(name, 0) + 1
        
        month_total_pairs = 0
        if session_ids:
            month_total_pairs = db.pairs.count_documents({'session_id': {'$in': session_ids}})
            
        stats = {'month': now.strftime('%m/%Y'), 'worker_stats': worker_stats, 'month_total_pairs': month_total_pairs}

        # Tìm kiếm
        search_query = request.args.get('q', '').strip()
        search_results = []
        if search_query:
            pairs = list(db.pairs.find({
                '$or': [{'source_cont': {'$regex': search_query, '$options': 'i'}},
                        {'target_cont': {'$regex': search_query, '$options': 'i'}}]
            }))
            for p in pairs:
                s = db.sessions.find_one({'_id': p['session_id']})
                if s:
                    p['work_date'] = s['work_date']
                    p['shift'] = s['shift']
                    search_results.append(p)
        
        # Danh sách lịch sử
        sessions = list(db.sessions.find().sort("work_date", -1))
        for s in sessions:
            s['pair_count'] = db.pairs.count_documents({'session_id': s['_id']})
            
        return render_template('home.html', sessions=sessions, stats=stats, search_results=search_results, search_query=search_query)
    except Exception as e:
        return f"Lỗi truy vấn: {e}"

@app.route('/create_session', methods=['POST'])
@login_required
def create_session():
    # User và Admin đều được tạo
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
@login_required
def delete_session(session_id):
    # CHỈ ADMIN ĐƯỢC XÓA
    if current_user.role != 'admin':
        return "🚫 Bạn không có quyền xóa! (Chỉ Admin mới được xóa)", 403

    try:
        s_id = ObjectId(session_id)
        pairs = db.pairs.find({'session_id': s_id})
        for pair in pairs:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_doc = db['fs.files'].find_one({"filename": filename})
                    if file_doc: fs.delete(file_doc['_id'])
        db.pairs.delete_many({'session_id': s_id})
        db.sessions.delete_one({'_id': s_id})
    except Exception as e:
        print(f"Lỗi xóa: {e}")
    return redirect(url_for('home'))

@app.route('/dashboard/<session_id>', methods=['GET', 'POST'])
@login_required
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
@login_required
def check_duplicate(session_id):
    data = request.get_json()
    source_cont = data.get('source_cont')
    existing = db.pairs.find_one({'session_id': ObjectId(session_id), 'source_cont': source_cont})
    return jsonify({'exists': True if existing else False})

@app.route('/update_pair/<pair_id>', methods=['POST'])
@login_required
def update_pair(pair_id):
    # ADMIN MỚI ĐƯỢC SỬA SỐ CONT (Tránh nhân viên sửa bậy)
    if current_user.role != 'admin':
         return "🚫 Chỉ Admin mới được sửa thông tin Cont!", 403
         
    try:
        p_id = ObjectId(pair_id)
        new_source = request.form.get('edit_source_cont')
        new_target = request.form.get('edit_target_cont')
        pair = db.pairs.find_one({'_id': p_id})
        if pair and new_source and new_target:
            db.pairs.update_one({'_id': p_id}, {'$set': {'source_cont': new_source, 'target_cont': new_target}})
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except: pass
    return redirect(url_for('home'))

@app.route('/delete_pair/<pair_id>')
@login_required
def delete_pair(pair_id):
    # CHỈ ADMIN ĐƯỢC XÓA
    if current_user.role != 'admin':
        return "🚫 Chỉ Admin mới được xóa cặp Cont!", 403
        
    try:
        p_id = ObjectId(pair_id)
        pair = db.pairs.find_one({'_id': p_id})
        if pair:
            if 'photos' in pair:
                for filename in pair['photos']:
                    file_doc = db['fs.files'].find_one({"filename": filename})
                    if file_doc: fs.delete(file_doc['_id'])
            db.pairs.delete_one({'_id': p_id})
            return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except: pass
    return redirect(url_for('home'))

@app.route('/image/<filename>')
def get_image(filename):
    try:
        file = fs.find_one({"filename": filename})
        if not file: return "Image not found", 404
        response = make_response(file.read())
        response.headers['Content-Type'] = 'image/jpeg'
        response.headers['Cache-Control'] = 'public, max-age=2592000'
        return response
    except Exception as e: return str(e)

@app.route('/upload_image/<pair_id>', methods=['POST'])
@login_required
def upload_image(pair_id):
    # Ai cũng được upload ảnh (cả admin và user)
    try:
        p_id = ObjectId(pair_id)
        pair = db.pairs.find_one({'_id': p_id})
        if not pair or 'photo' not in request.files: return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
        file = request.files['photo']
        if file.filename == '': return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
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
    except Exception as e: return f"Lỗi upload: {e}"

@app.route('/delete_image/<pair_id>/<filename>')
@login_required
def delete_image(pair_id, filename):
    # CHỈ ADMIN ĐƯỢC XÓA ẢNH
    if current_user.role != 'admin':
        return "🚫 Chỉ Admin mới được xóa ảnh!", 403

    try:
        file_doc = db['fs.files'].find_one({"filename": filename})
        if file_doc: fs.delete(file_doc['_id'])
        db.pairs.update_one({'_id': ObjectId(pair_id)}, {'$pull': {'photos': filename}})
        pair = db.pairs.find_one({'_id': ObjectId(pair_id)})
        return redirect(url_for('dashboard', session_id=str(pair['session_id'])))
    except Exception as e: return f"Lỗi xóa ảnh: {e}"

@app.route('/export_excel/<session_id>')
@login_required
def export_excel(session_id):
    # Ai cũng được xuất Excel
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
@login_required
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

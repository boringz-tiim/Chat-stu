import sys, os, json, socket, threading, sqlite3, hashlib
from PySide6.QtWidgets import QApplication, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QListWidget
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QIODevice, QObject, Signal
from PySide6.QtGui import QIcon
import time

ui_path = r"D:\python-work\Chat_stu\Server_stu\server.ui"
icon_path = r"D:\python-work\Chat_stu\Server_stu\icon.ico"

#获取当前IP
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except (OSError, socket.error):
        return "127.0.0.1"
    finally:
        s.close()
#数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), "user.db")

def hash_pw(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()
#初始化数据库
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()
#用户名注册
def register_user(username, password):
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return False, "用户名或密码不能为空"

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users(username, password_hash) VALUES(?,?)",
                    (username, hash_pw(password)))
        conn.commit()
        return True, "注册成功"
    except sqlite3.IntegrityError:
        return False, "用户名已存在"
    finally:
        conn.close()
#用户登录验证
def verify_user(username, password):
    username = (username or "").strip()
    password = password or ""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, "用户不存在"
    if row[0] != hash_pw(password):
        return False, "密码错误"
    return True, "登录成功"
#获取用户列表
def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT username FROM users ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

def send_json(conn, obj):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

def recv_line(conn: socket.socket, buf: bytearray):
    while True:
        i = buf.find(b"\n")
        if i != -1:
            line = bytes(buf[:i])
            del buf[:i+1]
            return line.decode("utf-8", errors="replace")

        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)

def recv_exact(conn: socket.socket, size: int):
    data = b""
    while len(data) < size:
        chunk = conn.recv(min(4096, size - len(data)))
        if not chunk:
            raise ConnectionError("接收文件中断")
        data += chunk
    return data

# ====== UI信号 ======
class UIEmitter(QObject):
    init_users = Signal(list)          # [(username, status, ip), ...]
    update_user = Signal(str, str, str)  # username, status, ip
    add_log = Signal(str)

class MainWindow:
    def __init__(self):
        # username -> {"status": "离线/在线", "ip": ""}
        self.user_states = {}
        # 在线连接：username -> conn
        self.online_conns = {}
        self.lock = threading.Lock()

        self.emitter = UIEmitter()

        self.window = None
        self.serverIPEdit = None
        self.serverPortEdit = None
        self.startButton = None
        self.stopButton = None
        self.userListWidget = None
        self.messageHistoryWidget = None
        self.server_thread = None

        self.load_ui()
        self.bind_signals()

        self.emitter.init_users.connect(self.ui_init_user_table)
        self.emitter.update_user.connect(self.ui_update_user_row)
        self.emitter.add_log.connect(self.ui_add_log)

        self.server_socket = None
        self.running = False

    def load_ui(self):
        loader = QUiLoader()
        file = QFile(ui_path)
        file.open(QIODevice.OpenModeFlag.ReadOnly)
        self.window = loader.load(file)
        file.close()

        self.window.setWindowTitle("服务端")
        self.window.setWindowIcon(QIcon(icon_path))

        self.serverIPEdit = self.window.findChild(QLineEdit, "serverIPEdit")
        self.serverPortEdit = self.window.findChild(QLineEdit, "serverPortEdit")
        self.startButton = self.window.findChild(QPushButton, "startButton")
        self.stopButton = self.window.findChild(QPushButton, "stopButton")

        self.serverIPEdit.setText(get_local_ip())
        self.serverPortEdit.setText("5000")

        self.userListWidget = self.window.findChild(QTableWidget, "userListWidget")
        self.userListWidget.setColumnCount(3)
        self.userListWidget.setHorizontalHeaderLabels(["用户名", "状态", "IP"])

        self.userListWidget.setColumnWidth(0,80)
        self.userListWidget.setColumnWidth(1, 80)
        self.userListWidget.setColumnWidth(2, 165)

        self.messageHistoryWidget = self.window.findChild(QListWidget, "messageHistoryWidget")
        # 在 load_ui 后添加
        self.serverIPEdit.setReadOnly(False)  # 确保可编辑
        # ✅ 设置自动换行
        self.messageHistoryWidget.setWordWrap(True)
        # ✅ 设置允许换行模式
        self.messageHistoryWidget.setResizeMode(QListWidget.ResizeMode.Adjust)
    def bind_signals(self):
        self.startButton.clicked.connect(self.start_server_thread)
        self.stopButton.clicked.connect(self.closeServer)

    # ====== UI更新 ======
    #初始化用户列表
    def ui_init_user_table(self, rows):
        table = self.userListWidget
        table.setRowCount(0)
        for username, status, ip in rows:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(username))
            table.setItem(r, 1, QTableWidgetItem(status))
            table.setItem(r, 2, QTableWidgetItem(ip))
    #更新用户列表
    def ui_update_user_row(self, username, status, ip):
        table = self.userListWidget
        target_row = -1
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            if item and item.text() == username:
                target_row = r
                break
        if target_row == -1:
            target_row = table.rowCount()
            table.insertRow(target_row)
            table.setItem(target_row, 0, QTableWidgetItem(username))
        table.setItem(target_row, 1, QTableWidgetItem(status))
        table.setItem(target_row, 2, QTableWidgetItem(ip))

    def ui_add_log(self, text: str):
        # 主线程更新 messageHistoryWidget
        if not self.messageHistoryWidget:
            return
        self.messageHistoryWidget.addItem(text)
        self.messageHistoryWidget.scrollToBottom()

    def log_event(self, who: str, what: str):
        # t = threading.current_thread().name  # 可选：调试线程用
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{now}：{who}{what}"
        self.emitter.add_log.emit(line)

    # ====== 状态列表（发给客户端） ======
    def build_user_state_payload(self):
        with self.lock:
            payload = []
            for u, v in self.user_states.items():
                payload.append({
                    "username": u,
                    "online": (v.get("status") == "在线"),
                    "ip": v.get("ip", "")
                })
            return payload

    def broadcast_user_state_list(self):
        """
            广播用户状态
            需要完成：
            1. 构造需要广播的msg，包括action和上面函数构造的payload
            2. 获取所有在线用户的socket
            3. 循环调用socket发送
            4. 调用函数，发送失败的标记为离线
            """
        payload = self.build_user_state_payload()
        msg={
            "action":"user_list",
            "users":payload

        }
        with self.lock:
            online_users=list(self.online_conns.items())
        failed_users=[]
        for username,conn in online_users:
            try:
                send_json(conn,msg)
            except Exception as e:
                print(f"发送用户列表给{username}失败:{e}")
                failed_users.append(username)
        for username in failed_users:
            self.mark_offline(username)

        print("TODO: 广播用户状态")

    def mark_offline(self, username):
        """
            标记离线用户
            需要完成：
            1. 将socket断开的用户标记为离线
            2. 如果该用户的socket还在online_conns则给它关掉
            3. 更新server用户表格
            4. 调用函数，广播给所有在线用户
            """
        with self.lock:
            if username not in self.online_conns:
                return
            conn=self.online_conns.get(username)
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    print(f"关闭{username}的连接时出错:{e}")
            del self.online_conns[username]
            #if username in self.online_conns:
            if username in self.user_states:
                self.user_states[username]["status"] = "离线"
                self.user_states[username]["ip"] = ""
        self.emitter.update_user.emit(username,"离线","")
        self.log_event(username,"已经标记为离线")
        self.broadcast_user_state_list()
        print("TODO: 更新离线用户状态")

    # ====== 点击启动 ======
    def start_server_thread(self):
        init_db()

        usernames = get_all_users()
        with self.lock:
            self.user_states = {u: {"status": "离线", "ip": ""} for u in usernames}
            self.online_conns = {}

        init_rows = [(u, "离线", "") for u in usernames]
        self.emitter.init_users.emit(init_rows)

        self.log_event("系统",
                       f"服务端启动，监听 {self.serverIPEdit.text().strip()}:{self.serverPortEdit.text().strip()}，已加载用户 {len(usernames)} 个")

        self.running = True
        if self.running:
            self.startButton.setEnabled(False)
            self.stopButton.setEnabled(True)

        self.server_thread = threading.Thread(
            #核心监听循环，持续监听客户端连接请求，并为每个连接的客户端创建独立的处理线程
            target=self.server_loop,
            daemon=True
        )
        self.server_thread.start()

    def closeServer(self):
        """
            关闭服务器
            需要完成：
            1. 提示服务器已关闭
            2. 对所有在线用户发送json（包括action和msg），说明服务器已关闭
            3. 关闭所有socket链接
            4. 关闭线程
            """
        if not self.running:
            return
        self.running = False
        self.log_event("系统","服务器正在关闭...")
        with self.lock:
            online_users=list(self.online_conns.items())
        close_msg={
            "action":"server_close",
            "msg":"服务器已经关闭"
        }
        for username,conn in online_users:
            try:
                send_json(conn,close_msg)
            except Exception as e:
                print(f"通知{username}时出错:{e}")
        with self.lock:
            for username,conn in self.online_conns.items():
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except (OSError,ConnectionError):
                    pass
                try:
                    conn.close()
                except (OSError, ConnectionError):
                    pass
            self.online_conns.clear()
        if self.server_socket:
            try:
                self.server_socket.close()
            except (OSError, AttributeError):
                pass
        self.startButton.setEnabled(True)
        self.stopButton.setEnabled(False)
        self.log_event("系统", f"服务器已关闭，断开 {len(online_users)} 个客户端连接")

        print("TODO: 关闭服务器")

    def server_loop(self):
        serverIP = self.serverIPEdit.text().strip()
        serverPort = int(self.serverPortEdit.text().strip())

        self.server_socket = socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM
        )
        self.server_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_REUSEADDR,
            1
        )
        self.server_socket.bind((serverIP, serverPort))
        self.server_socket.listen(50)
        print(f"[SERVER] listening on {serverIP}:{serverPort}")

        self.server_socket.settimeout(1)
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(
                    target=self.handle_client,
                    args=(conn, addr),
                    daemon=True
                ).start()

            except socket.timeout:
                continue
            except OSError:
                break

    def handle_client(self, conn, addr):
        print("[SERVER] client:", addr)
        buf = bytearray()
        bound_username = None  # 这个连接对应的在线用户名（如果进入online）

        try:
            while True:
                line = recv_line(conn, buf)
                if line is None:
                    break

                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    send_json(conn, {"ok": False, "msg": "请求不是合法JSON"})
                    continue

                action = req.get("action")
                username = (req.get("username") or "").strip()
                password = req.get("password") or ""
                client_ip = (req.get("client_ip") or "").strip() or addr[0]
                #用户注册处理
                if action == "register":
                    ok, msg = register_user(username, password)
                    self.log_event(username or "未知用户", f"尝试注册（结果：{msg}）")
                    if ok:
                        # 新用户也加入状态表：离线
                        with self.lock:
                            self.user_states.setdefault(username, {"status": "离线", "ip": ""})
                        self.emitter.update_user.emit(username, "离线", "")
                        self.broadcast_user_state_list()
                    send_json(conn, {"ok": ok, "msg": msg})

                elif action == "login":
                    ok, msg = verify_user(username, password)
                    self.log_event(username or "未知用户", f"尝试登录校验（结果：{msg}）")
                    if ok:
                        # 这里只做“校验成功回包”，真正上线由 online 来确定长连接
                        send_json(conn, {"ok": True, "msg": msg})
                    else:
                        send_json(conn, {"ok": False, "msg": msg})

                elif action == "online":
                    # 更新用户状态
                    # 需要完成：
                    # 1. 判断用户名是否为空
                    # 2. 将用户的状态、IP更新
                    # 3. 更新服务端用户列表的表格
                    # 4. 调用函数，给刚上线的用户推一次全量列表 + 广播给所有在线用户
                    if not username:
                        send_json(conn,{"ok":False,"msg":"用户名不能为空"})
                        continue
                    #更新用户状态，IP更新
                    with self.lock:
                        if username not in self.user_states:
                            send_json(conn,{"ok":False,"msg":"用户不存在，请先注册"})
                            continue
                        if username in self.online_conns:
                            send_json(conn,{"ok":False,"msg":"用户已经在其他地方登录"})
                            continue
                        #更新用户状态
                        self.user_states[username]["status"]="在线"
                        self.user_states[username]["ip"]=client_ip
                        self.online_conns[username]=conn
                        bound_username=username
                    #更新用户列表
                    self.emitter.update_user.emit(username,"在线",client_ip)
                    self.log_event(username,"上线了")
                    #广播给所有在线用户
                    send_json(conn,{"ok":True,"msg":"上线成功"})
                    self.broadcast_user_state_list()
                    online_notify = {
                        "action": "user_online",
                        "username": username,
                        "ip": client_ip
                    }
                    with self.lock:
                        for uname, Conn in self.online_conns.items():
                            if uname != username:  # 不发给自己
                                try:
                                    send_json(Conn, online_notify)
                                except (OSError, ConnectionError, BrokenPipeError):
                                    pass
                    print("TODO: 实现用户状态更新")

                elif action in("private_msg","public_msg"):
                        # 分发消息
                        # 需要完成：
                        # 1. 获取from和to
                        # 2. 判断是群聊还是私聊
                        # 3. 获取to的socket
                        # 4.
                        # 5. 调用socket发送消息json
                    from_user=req.get("from","").strip()
                    to_user=req.get("to","").strip()
                    content=req.get("content","").strip()
                    timestamp=req.get("timestamp","").strip()
                    # if action == "private_msg":
                    #     forward_msg = {
                    #         "action": "private_msg",
                    #         "from": from_user,
                    #         "content": content,
                    #         "timestamp": timestamp
                    #     }

                    if not from_user:
                        send_json(conn,{"ok":False,"msg":"发送者不能为空"})
                        continue
                    if not content:
                        send_json(conn,{"ok":False,"msg":"消息内容不能为空"})
                        continue
                    if action == "public_msg":
                        forward_msg = {
                            "action": "public_msg",
                            "from": from_user,
                            "content": content,
                            "timestamp": timestamp
                        }
                        """
                    if to_user=="__ALL__" or msg_type=="public":
                        forward_msg={
                            "action":"public_msg",
                            "from":from_user,
                            "content":content,
                            "timestamp":timestamp

                        }
                        """
                        with self.lock:
                            online_users=list(self.online_conns.items())
                            sent_count=0
                            for username,target_conn in online_users:
                                if username != from_user:
                                    try:
                                        send_json(target_conn,forward_msg)
                                        sent_count+=1
                                    except Exception as e:
                                        print(f"发送给群聊{username}失败:{e}")
                            self.log_event(from_user,f"发送群聊消息，送达{sent_count}人")
                            send_json(conn,{"ok":True,"msg":f"群聊消息已经发送给{sent_count}人"})
                    else:
                        if not to_user:
                            send_json(conn,{"ok":False,"msg":"接收者不能为空"})
                            continue

                        forward_msg = {
                                "action": "private_msg",
                                "from": from_user,
                                "content": content,
                                "timestamp": timestamp
                            }
                        with self.lock:
                            if to_user not in self.online_conns:
                                self.log_event(from_user,f"尝试发送消息给离线用户{to_user}")
                                send_json(conn,{"ok":False,"msg":f"用户{to_user}不在线"})
                                continue
                            target_conn=self.online_conns[to_user]
                            try:
                                send_json(target_conn,forward_msg)
                                self.log_event(from_user,f"发送私聊消息给{to_user}")
                                send_json(conn,{"ok":True,"msg":"发送成功"})
                            except Exception as e:
                                self.log_event("系统",f"发送消息给{to_user}失败:{e}")
                                send_json(conn,{"ok":False,"msg":"发送失败"})

                    print("TODO: 实现消息分发")

                elif action in ("file_meta","file_transfer"):
                    # 分发文件
                    # 需要完成：
                    # 1. 获取from和to
                    # 2. 获取to的socket
                    # 3. 如果to不在线要如何处理
                    # 4. 调用socket发送文件meta
                    # 5. 接收文件内容
                    # 6. 转发文件内容
                    from_user=req.get("from","").strip()
                    to_user=req.get("to","").strip()
                    filename=req.get("filename","").strip()
                    file_size=req.get("size",0)
                    if not from_user:
                        send_json(conn,{"ok":False,"msg":"发送者不能为空"})
                        continue
                    if not to_user:
                        send_json(conn,{"ok":False,"msg":"接收者不能为空"})
                        continue
                    if not filename:
                        send_json(conn,{"ok":False,"msg":"文件名不能为空"})
                        continue
                    if file_size<=0:
                        send_json(conn,{"ok":False,"msg":"文件大小无效"})
                        continue
                    with self.lock:
                        if to_user not in self.online_conns:
                            self.log_event(from_user,f"尝试发文件给离线用户{to_user}")
                            #连线接收文件是一个可拓展点
                            send_json(conn,{"ok":False,"msg":f"用户{to_user}不在线无法发送文件"})
                            continue
                        target_conn=self.online_conns[to_user]
                    file_meta={
                        "action":"file_transfer",
                        "from":from_user,
                        "filename":filename,
                        "size":file_size
                    }
                    try:
                        # 发送文件元数据给目标用户
                        send_json(target_conn, file_meta)
                        self.log_event(from_user, f"请求发送文件 {filename} 给 {to_user}，大小: {file_size} bytes")

                        # 等待接收方确认（是否准备好接收）
                        # 等接收方确认
                        resp_line = recv_line(target_conn, bytearray())
                        if not resp_line:
                            send_json(conn, {"ok": False, "msg": "接收方无响应"})
                            continue

                        resp = json.loads(resp_line)
                        if not resp.get("ok"):
                            send_json(conn, {"ok": False, "msg": "接收方拒绝"})
                            continue

                        # 通知发送方可以开始发送文件内容
                        send_json(conn, {"ok": True, "msg": "接收方已准备好，请发送文件内容"})

                        # 5. 接收文件内容
                        # 6. 转发文件内容
                        received = 0
                        chunk_count = 0

                        while received < file_size:
                            # 计算本次要接收的数据大小
                            chunk_size = min(4096, file_size - received)

                            # 接收文件数据块
                            chunk = recv_exact(conn, chunk_size)
                            if not chunk:
                                raise ConnectionError("文件传输中断")

                            # 转发给目标用户
                            target_conn.sendall(chunk)

                            received += len(chunk)
                            chunk_count += 1

                            # 可选：每10个块打印一次进度
                            if chunk_count % 10 == 0:
                                progress = (received / file_size) * 100
                                print(f"文件传输进度: {progress:.1f}% ({received}/{file_size})")

                        # 传输完成
                        self.log_event(from_user, f"文件 {filename} 已成功发送给 {to_user}")
                        send_json(conn, {"ok": True, "msg": "文件发送成功"})
                    except Exception as e:
                        self.log_event("系统", f"文件传输失败: {e}")
                        send_json(conn, {"ok": False, "msg": f"文件传输失败: {e}"})


                elif action == "file_notify":
                    # 群聊文件通知
                    from_user = req.get("from", "").strip()
                    #to_group = req.get("to", "").strip()
                    filename = req.get("filename", "")
                    file_size = req.get("size", 0)

                    # 生成文件ID
                    import uuid
                    file_id = str(uuid.uuid4())

                    # 广播给所有在线用户（除发送者自己）
                    notify_msg = {
                        "action": "file_notify",
                        "from": from_user,
                        "filename": filename,
                        "size": file_size,
                        "file_id": file_id
                    }

                    with self.lock:
                        online_users = list(self.online_conns.items())

                    sent_count = 0
                    for username, target_conn in online_users:
                        if username != from_user:
                            try:
                                send_json(target_conn, notify_msg)
                                sent_count += 1
                            except Exception:
                                pass

                    send_json(conn, {"ok": True, "msg": f"文件通知已发送给 {sent_count} 人"})
                    self.log_event(from_user, f"分享文件 {filename}，通知 {sent_count} 人")

                    print("TODO: 实现文件分发")

                else:
                    send_json(conn, {"ok": False, "msg": "未知action"})

        except Exception as e:
            print("[SERVER] error:", e)
            import traceback
            traceback.print_exc()
        finally:
            # 连接断开：如果这个连接绑定了在线用户，则置离线并广播
            try:
                conn.close()
            except  (OSError, ConnectionError):
                pass

            if bound_username:
                self.log_event(bound_username, "下线（连接断开）")
                self.mark_offline(bound_username)

app = QApplication(sys.argv)
w = MainWindow()
w.window.show()
sys.exit(app.exec())

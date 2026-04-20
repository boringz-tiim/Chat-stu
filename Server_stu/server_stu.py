import sys, os, json, socket, threading, sqlite3, hashlib, time
from PySide6.QtWidgets import QApplication, QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QListWidget
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QIODevice, QObject, Signal
from PySide6.QtGui import QIcon

ui_path = r"D:\python-work\Chat_stu\Server_stu\server.ui"
icon_path = r"D:\python-work\Chat_stu\Server_stu\icon.ico"

db_lock = threading.Lock()


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except (OSError, socket.error):
        return "127.0.0.1"
    finally:
        s.close()


DB_PATH = os.path.join(os.path.dirname(__file__), "user.db")


def hash_pw(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
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


def register_user(username, password):
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return False, "用户名或密码不能为空"

    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
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


def verify_user(username, password):
    username = (username or "").strip()
    password = password or ""

    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM users WHERE username=?", (username,))
        row = cur.fetchone()
        conn.close()

    if not row:
        return False, "用户不存在"
    if row[0] != hash_pw(password):
        return False, "密码错误"
    return True, "登录成功"


def get_all_users():
    with db_lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT username FROM users ORDER BY id ASC")
        rows = cur.fetchall()
        conn.close()
    return [r[0] for r in rows]


def send_json(conn, obj):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def recv_line(conn: socket.socket, buf: bytearray):
    """从socket接收一行数据，无限等待"""
    while True:
        i = buf.find(b"\n")
        if i != -1:
            line = bytes(buf[:i])
            del buf[:i + 1]
            return line.decode("utf-8", errors="replace")
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf.extend(chunk)


def recv_exact(conn: socket.socket, size: int, timeout=60):
    """接收固定大小的数据，文件传输时使用超时"""
    conn.settimeout(timeout)
    data = b""
    try:
        while len(data) < size:
            chunk = conn.recv(min(4096, size - len(data)))
            if not chunk:
                raise ConnectionError("接收文件中断")
            data += chunk
        return data
    except socket.timeout:
        raise TimeoutError(f"文件接收超时（{timeout}秒）")
    finally:
        conn.settimeout(None)


class UIEmitter(QObject):
    init_users = Signal(list)
    update_user = Signal(str, str, str)
    add_log = Signal(str)


class MainWindow:
    def __init__(self):
        self.user_states = {}
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
        self.userListWidget.setColumnWidth(0, 80)
        self.userListWidget.setColumnWidth(1, 80)
        self.userListWidget.setColumnWidth(2, 165)

        self.messageHistoryWidget = self.window.findChild(QListWidget, "messageHistoryWidget")
        self.serverIPEdit.setReadOnly(False)
        self.messageHistoryWidget.setWordWrap(True)
        self.messageHistoryWidget.setResizeMode(QListWidget.ResizeMode.Adjust)

    def bind_signals(self):
        self.startButton.clicked.connect(self.start_server_thread)
        self.stopButton.clicked.connect(self.closeServer)

    def ui_init_user_table(self, rows):
        table = self.userListWidget
        table.setRowCount(0)
        for username, status, ip in rows:
            r = table.rowCount()
            table.insertRow(r)
            table.setItem(r, 0, QTableWidgetItem(username))
            table.setItem(r, 1, QTableWidgetItem(status))
            table.setItem(r, 2, QTableWidgetItem(ip))

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
        if not self.messageHistoryWidget:
            return
        self.messageHistoryWidget.addItem(text)
        self.messageHistoryWidget.scrollToBottom()

    def log_event(self, who: str, what: str):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{now}：{who}{what}"
        self.emitter.add_log.emit(line)

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
        payload = self.build_user_state_payload()
        msg = {"action": "user_list", "users": payload}

        with self.lock:
            online_users = list(self.online_conns.items())

        failed_users = []
        for username, conn in online_users:
            try:
                send_json(conn, msg)
            except Exception as e:
                print(f"发送用户列表给{username}失败:{e}")
                failed_users.append(username)

        for username in failed_users:
            self.mark_offline(username)

    def mark_offline(self, username):
        with self.lock:
            if username not in self.online_conns:
                return
            conn = self.online_conns.get(username)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            del self.online_conns[username]
            if username in self.user_states:
                self.user_states[username]["status"] = "离线"
                self.user_states[username]["ip"] = ""

        self.emitter.update_user.emit(username, "离线", "")
        self.log_event(username, "已经标记为离线")

        offline_msg = {"action": "user_offline", "username": username}
        with self.lock:
            for uname, conn in self.online_conns.items():
                if uname != username:
                    try:
                        send_json(conn, offline_msg)
                    except Exception:
                        pass

        self.broadcast_user_state_list()

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

        self.server_thread = threading.Thread(target=self.server_loop, daemon=True)
        self.server_thread.start()

    def closeServer(self):
        if not self.running:
            return

        self.running = False
        self.log_event("系统", "服务器正在关闭...")

        with self.lock:
            online_users = list(self.online_conns.items())

        close_msg = {"action": "server_close", "msg": "服务器已经关闭"}
        for username, conn in online_users:
            try:
                send_json(conn, close_msg)
            except Exception:
                pass

        with self.lock:
            for username, conn in self.online_conns.items():
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except (OSError, ConnectionError):
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

    def server_loop(self):
        serverIP = self.serverIPEdit.text().strip()
        serverPort = int(self.serverPortEdit.text().strip())

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((serverIP, serverPort))
        self.server_socket.listen(50)
        print(f"[SERVER] listening on {serverIP}:{serverPort}")

        self.server_socket.settimeout(1)
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
                threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def handle_client(self, conn, addr):
        print("[SERVER] client:", addr)
        buf = bytearray()
        bound_username = None

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

                if action == "register":
                    ok, msg = register_user(username, password)
                    self.log_event(username or "未知用户", f"尝试注册（结果：{msg}）")
                    if ok:
                        with self.lock:
                            self.user_states.setdefault(username, {"status": "离线", "ip": ""})
                        self.emitter.update_user.emit(username, "离线", "")
                        self.broadcast_user_state_list()
                    send_json(conn, {"ok": ok, "msg": msg})

                elif action == "login":
                    ok, msg = verify_user(username, password)
                    self.log_event(username or "未知用户", f"尝试登录校验（结果：{msg}）")
                    if ok:
                        send_json(conn, {"ok": True, "msg": msg})
                    else:
                        send_json(conn, {"ok": False, "msg": msg})

                elif action == "online":
                    if not username:
                        send_json(conn, {"ok": False, "msg": "用户名不能为空"})
                        continue

                    with self.lock:
                        if username not in self.user_states:
                            send_json(conn, {"ok": False, "msg": "用户不存在，请先注册"})
                            continue
                        if username in self.online_conns:
                            send_json(conn, {"ok": False, "msg": "用户已经在其他地方登录"})
                            continue

                        self.user_states[username]["status"] = "在线"
                        self.user_states[username]["ip"] = client_ip
                        self.online_conns[username] = conn
                        bound_username = username

                    self.emitter.update_user.emit(username, "在线", client_ip)
                    self.log_event(username, "上线了")
                    send_json(conn, {"ok": True, "msg": "上线成功"})
                    self.broadcast_user_state_list()

                    online_notify = {"action": "user_online", "username": username, "ip": client_ip}
                    with self.lock:
                        for uname, target_conn in self.online_conns.items():
                            if uname != username:
                                try:
                                    send_json(target_conn, online_notify)
                                except (OSError, ConnectionError, BrokenPipeError):
                                    pass

                elif action == "public_msg":
                    from_user = req.get("from", "").strip()
                    msg_type = req.get("type", "text")
                    timestamp = req.get("timestamp", "")

                    if not from_user:
                        send_json(conn, {"ok": False, "msg": "发送者不能为空"})
                        continue

                    # 公聊文件
                    if msg_type == "file":
                        filename = req.get("filename", "").strip()
                        data = req.get("data", "")

                        if not filename or not data:
                            send_json(conn, {"ok": False, "msg": "文件信息不完整"})
                            continue

                        forward_msg = {
                            "action": "public_msg",
                            "type": "file",
                            "from": from_user,
                            "filename": filename,
                            "data": data,
                            "timestamp": timestamp
                        }

                    # 公聊文本
                    else:
                        content = req.get("content", "").strip()
                        if not content:
                            send_json(conn, {"ok": False, "msg": "消息内容不能为空"})
                            continue

                        forward_msg = {
                            "action": "public_msg",
                            "from": from_user,
                            "content": content,
                            "timestamp": timestamp
                        }

                    with self.lock:
                        online_users = list(self.online_conns.items())

                    sent_count = 0
                    sent_set = set()
                    for uname, target_conn in online_users:
                        if uname != from_user and uname not in sent_set:
                            try:
                                send_json(target_conn, forward_msg)
                                sent_count += 1
                                sent_set.add(uname)
                            except Exception as e:
                                print(f"发送给{uname}失败:{e}")

                    send_json(conn, {"ok": True, "msg": f"群聊消息已发送给{sent_count}人"})
                    self.log_event(from_user, f"发送群聊消息，送达{sent_count}人")
                # elif action == "public_msg":
                #     from_user = req.get("from", "")
                #     content = req.get("content", "")
                #     timestamp = req.get("timestamp", "")
                #
                #     forward_msg = {
                #         "action": "public_msg",
                #         "from": from_user,
                #         "content": content,
                #         "timestamp": timestamp
                #     }
                #
                #     with self.lock:
                #         for uname, c in self.online_conns.items():
                #             if uname != from_user:
                #                 try:
                #                     send_json(c, forward_msg)
                #                 except:
                #                     pass
                #
                #     send_json(conn, {"ok": True, "msg": "群发成功"})
                # elif action == "public_file":
                #     from_user = req.get("from")
                #     filename = req.get("filename")
                #     data = req.get("data")
                #
                #     for uname, target_conn in self.online_conns.items():
                #         if uname != from_user:
                #             send_json(target_conn, {
                #                 "action": "public_msg",
                #                 "type": "file",
                #                 "from": from_user,
                #                 "filename": filename,
                #                 "data": data
                #             })
                #
                #     send_json(conn, {"ok": True, "msg": "公聊文件已发送"})
                # elif action == "private_msg":
                #     from_user = req.get("from", "").strip()
                #     to_user = req.get("to", "").strip()
                #     content = req.get("content", "").strip()
                #     timestamp = req.get("timestamp", "")
                #
                #     forward_msg = {
                #         "action": "private_msg",
                #         "from": from_user,
                #         "content": content,
                #         "timestamp": timestamp
                #     }
                #
                #     with self.lock:
                #         target_conn = self.online_conns.get(to_user)
                #
                #     if target_conn:
                #         try:
                #             send_json(target_conn, forward_msg)
                #             send_json(conn, {"ok": True, "msg": "发送成功"})
                #         except:
                #             send_json(conn, {"ok": False, "msg": "发送失败"})
                #     else:
                #         send_json(conn, {"ok": False, "msg": "用户不在线"})
                elif action == "private_msg":
                    from_user = req.get("from", "").strip()
                    to_user = req.get("to", "").strip()

                    with self.lock:
                        if to_user not in self.online_conns:
                            send_json(conn, {"ok": False, "msg": f"用户{to_user}不在线"})
                            continue

                        target_conn = self.online_conns[to_user]

                    try:
                        # ✅ 直接转发整个请求（关键！）
                        send_json(target_conn, req)

                        send_json(conn, {"ok": True, "msg": "发送成功"})
                    except Exception as e:
                        send_json(conn, {"ok": False, "msg": "发送失败"})
                elif action == "file_transfer":
                    from_user = req.get("from", "").strip()
                    to_user = req.get("to", "").strip()
                    filename = req.get("filename", "").strip()
                    file_size = req.get("size", 0)


                    if not from_user or not to_user or not filename or file_size <= 0:
                        send_json(conn, {"ok": False, "msg": "文件信息不完整"})
                        continue

                    with self.lock:
                        if to_user not in self.online_conns:
                            self.log_event(from_user, f"尝试发文件给离线用户{to_user}")
                            send_json(conn, {"ok": False, "msg": f"用户{to_user}不在线，无法发送文件"})
                            continue
                        target_conn = self.online_conns[to_user]

                    file_meta = {
                        "action": "file_transfer",
                        "from": from_user,
                        "filename": filename,
                        "size": file_size
                    }

                    try:
                        send_json(target_conn, file_meta)
                        self.log_event(from_user, f"请求发送文件 {filename} 给 {to_user}，大小: {file_size} bytes")

                        recv_buf = bytearray()
                        resp_line = recv_line(target_conn, recv_buf)
                        if not resp_line:
                            send_json(conn, {"ok": False, "msg": "接收方无响应"})
                            continue

                        resp = json.loads(resp_line)
                        if not resp.get("ok"):
                            send_json(conn, {"ok": False, "msg": "接收方拒绝接收文件"})
                            continue

                        send_json(conn, {"ok": True, "msg": "接收方已准备好，请发送文件内容"})

                        send_buf = bytearray()
                        start_msg = recv_line(conn, send_buf)
                        if not start_msg:
                            raise ConnectionError("发送方无响应")

                        start_resp = json.loads(start_msg)
                        if not start_resp.get("ok"):
                            raise Exception(start_resp.get("msg", "发送方取消"))

                        received = 0
                        conn.settimeout(60)
                        target_conn.settimeout(60)
                        try:
                            while received < file_size:
                                chunk_size = min(4096, file_size - received)
                                chunk = recv_exact(conn, chunk_size, timeout=60)
                                target_conn.sendall(chunk)
                                received += len(chunk)
                        finally:
                            conn.settimeout(None)
                            target_conn.settimeout(None)

                        send_json(conn, {"ok": True, "msg": "文件发送成功"})
                        self.log_event(from_user, f"文件 {filename} 已成功发送给 {to_user}")

                    except TimeoutError as e:
                        self.log_event("系统", f"文件传输超时: {e}")
                        send_json(conn, {"ok": False, "msg": f"文件传输超时: {e}"})
                    except Exception as e:
                        self.log_event("系统", f"文件传输失败: {e}")
                        send_json(conn, {"ok": False, "msg": f"文件传输失败: {e}"})

                elif action == "file_notify":
                    from_user = req.get("from", "").strip()
                    filename = req.get("filename", "")
                    file_size = req.get("size", 0)

                    notify_msg = {
                        "action": "file_notify",
                        "from": from_user,
                        "filename": filename,
                        "size": file_size
                    }

                    with self.lock:
                        online_users = list(self.online_conns.items())

                    sent_count = 0
                    sent_set = set()  # 防止重复发送
                    for uname, target_conn in online_users:
                        if uname != from_user and uname not in sent_set:
                            try:
                                send_json(target_conn, notify_msg)
                                sent_count += 1
                                sent_set.add(uname)
                            except Exception:
                                pass

                    send_json(conn, {"ok": True, "msg": f"文件通知已发送给 {sent_count} 人"})
                    self.log_event(from_user, f"分享文件 {filename}，通知 {sent_count} 人")

                else:
                    send_json(conn, {"ok": False, "msg": "未知action"})

        except Exception as e:
            print(f"[SERVER] error for {bound_username}: {e}")
        finally:
            try:
                conn.close()
            except (OSError, ConnectionError):
                pass

            if bound_username:
                self.log_event(bound_username, "下线（连接断开）")
                self.mark_offline(bound_username)


app = QApplication(sys.argv)
w = MainWindow()
w.window.show()
sys.exit(app.exec())
import sys, os, json, socket, threading
from PySide6.QtWidgets import (
    QApplication, QStackedWidget, QPushButton, QLineEdit, QMessageBox,
    QListWidget, QListWidgetItem, QTextEdit, QLabel, QWidget, QHBoxLayout, QToolButton,
    QFileDialog
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QIODevice, QObject, Signal, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QAbstractItemView
import ipaddress
import re
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from datetime import datetime
# from PySide6.QtWidgets import QListWidgetItem

ui_path = r"D:\python-work\Chat_stu\Client_stu\client.ui"
icon_path = r"D:\python-work\Chat_stu\Client_stu\icon.ico"
eye_open = r"D:\python-work\Chat_stu\Client_stu\eye_open.ico"
eye_close = r"D:\python-work\Chat_stu\Client_stu\eye_close.ico"
"""
向服务器发送请求，并等待服务器返回结果
"""
def send_request(server_ip, server_port, req):
    with socket.create_connection((server_ip, server_port), timeout=5) as sock:
        sock.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = bytearray()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("服务端断开连接")
            buf.extend(chunk)
            i = buf.find(b"\n")
            if i != -1:
                line = bytes(buf[:i]).decode("utf-8", errors="replace")
                return json.loads(line)

def getLocalIp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except (OSError, socket.error):
        return "127.0.0.1"
    finally:
        s.close()
#从socket里度一整行数据
#conn:socket.socket一条和服务器之间的通信管道
#接收JSON消息
def recv_line(conn: socket.socket, buf: bytearray):
    while True:
        i = buf.find(b"\n")
        if i != -1:#已经收到了完整的一条消息
            line = bytes(buf[:i])
            del buf[:i + 1]
            return line.decode("utf-8", errors="replace")
        chunk = conn.recv(4096)#从网络中读到的一块数据
        if not chunk:
            return None
        buf.extend(chunk)#buf将这一下快数据拼起来，直到遇到换行符，取出一条完整的数据
#接收文件内容
def recv_exact(conn: socket.socket, size: int):
    data = b""
    while len(data) < size:
        chunk = conn.recv(min(4096, size - len(data)))
        if not chunk:
            raise ConnectionError("文件接收中断")
        data += chunk
    return data
#将字典转换为JSON字符串添加换行符通过socket发送
def send_json(conn: socket.socket, obj: dict):
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
#跨线程信号发射器 - 用于在工作线程中安全地更新UI
class UiEmitter(QObject):
    users_update = Signal(list)            # list[dict]
    msg_in = Signal(str, str, object)         # from, to, text
    info = Signal(str)

class MainWindow:
    #类的构造函数，当创建对象时自动执行，用于初始化对象的初始状态
    def __init__(self):
        self.sock = None
        self.recv_thread = None
        self.stop_recv = threading.Event()
        self.sock_lock=threading.Lock()

        self.username = ""
        self.local_ip = getLocalIp()

        # peer -> {"messages":[(is_me,text)], "unread":int, "online":bool, "ip":str}
        self.sessions = {}
        self.current_peer = None

        self.server_closed = False
        #创建信号发射器，用于跨线程通信
        self.emitter = UiEmitter()

        #预先声明
        # 预先声明所有UI控件属性
        self.window=None
        self.interfaceWidget = None
        self.loginButton = None
        self.enrollButton = None
        self.userNameEdit = None
        self.userPwdEdit = None
        self.enrollUserNameEdit = None
        self.enrollUserPwdEdit = None  # ← 预先声明
        self.enrollSummitButton = None
        self.enrollServerIPEdit = None
        self.enrollServerPortEdit = None
        self.serverIPEdit = None
        self.serverPortEdit = None
        self.loginEyeButton = None
        self.chatListWidget = None
        self.messageListWidget = None
        self.inputTextEdit = None
        self.sendMsgButton = None
        self.sendFileButton = None



        self.load_ui()
        self.bind_signals()

        self.emitter.users_update.connect(self.on_users_update)
        self.emitter.msg_in.connect(self.on_message_in)
        self.emitter.info.connect(lambda s: self.popUp("提示", s, ok=True))

        self.last_msg_time = None

        # ⭐ 公聊会话（固定存在）
        self.sessions["__ALL__"] = {
            "messages": [],
            "unread": 0,
            "online": True,
            "ip": ""
        }

    def load_ui(self):
        loader = QUiLoader()
        file = QFile(ui_path)
        file.open(QIODevice.OpenModeFlag.ReadOnly)
        self.window = loader.load(file)
        file.close()

        self.window.setWindowTitle("客户端")
        self.window.setWindowIcon(QIcon(icon_path))

        self.interfaceWidget = self.window.findChild(QStackedWidget, "interfaceWidget")
        self.loginButton = self.window.findChild(QPushButton, "loginButton")
        self.enrollButton = self.window.findChild(QPushButton, "enrollButton")

        self.userNameEdit = self.window.findChild(QLineEdit, "userNameEdit")
        self.userPwdEdit = self.window.findChild(QLineEdit, "userPwdEdit")

        self.enrollUserNameEdit = self.window.findChild(QLineEdit, "enrollUserNameEdit")
        self.enrollUserPwdEdit = self.window.findChild(QLineEdit, "enrollUserPwdEdit")
        self.enrollSummitButton = self.window.findChild(QPushButton, "enrollSummitButton")
        self.enrollServerIPEdit = self.window.findChild(QLineEdit, "enrollServerIPEdit")
        self.enrollServerPortEdit = self.window.findChild(QLineEdit, "enrollServerPortEdit")

        self.serverIPEdit = self.window.findChild(QLineEdit, "serverIPEdit")
        self.serverPortEdit = self.window.findChild(QLineEdit, "serverPortEdit")

        self.loginEyeButton = self.window.findChild(QToolButton, "loginEyeButton")
        if self.userPwdEdit:
            self.userPwdEdit.setEchoMode(QLineEdit.EchoMode.Password)
        if self.loginEyeButton:
            self.loginEyeButton.setCheckable(True)
            self.loginEyeButton.setIcon(QIcon(eye_close))

        # 聊天页控件（index=2）
        self.chatListWidget = self.window.findChild(QListWidget, "chatListWidget")
        self.messageListWidget = self.window.findChild(QListWidget, "messageListWidget")
        self.inputTextEdit = self.window.findChild(QTextEdit, "inputTextEdit")
        self.sendMsgButton = self.window.findChild(QPushButton, "sendMsgButton")
        self.sendFileButton = self.window.findChild(QPushButton, "sendFileButton")

        self.messageListWidget.setSpacing(3)

        # 左侧用户列表：选中高亮（QSS）
        if self.chatListWidget:
            self.chatListWidget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.chatListWidget.setStyleSheet("""
                QListWidget {
                    background: #FFFFFF;
                    border: 1px solid #E5E5E5;
                    outline: 0;
                }
                QListWidget::item {
                    padding: 10px 8px;
                    margin: 2px 6px;
                    border-radius: 8px;
                }
                QListWidget::item:hover {
                    background: #F2F6FF;
                }
                QListWidget::item:selected {
                    background: #DCEBFF;
                    color: #003A8C;
                    font-weight: 600;
                }
            """)

        # 连接时用 lambda 明确参数
        self.messageListWidget.itemDoubleClicked.connect(self.open_file) #type: ignore

    def bind_signals(self):
        self.loginButton.clicked.connect(self.do_login)
        self.enrollButton.clicked.connect(lambda: self.interfaceWidget.setCurrentIndex(1))
        self.enrollSummitButton.clicked.connect(self.do_register)

        if self.sendMsgButton:
            self.sendMsgButton.clicked.connect(self.send_chat_message)
        if self.chatListWidget:
            self.chatListWidget.itemClicked.connect(self.on_peer_clicked)
        if self.sendFileButton:
            self.sendFileButton.clicked.connect(self.send_file)

        if self.loginEyeButton and self.userPwdEdit:
            self.loginEyeButton.toggled.connect(
                lambda checked: self.toggle_password(self.userPwdEdit, self.loginEyeButton, checked))
#显示一个弹出消息框，用于提示用户信息或警告错误
#True 信息框 False 警告框
    def popUp(self, title, msg, ok=True):
        if ok:
            QMessageBox.information(self.window, title, msg)
        else:
            QMessageBox.warning(self.window, title, msg)
    @staticmethod
    def isValidIp(ip):
        """
            判断IP是否合法
            """
        if not ip or not isinstance(ip,str):
             return False
        try:
            ipaddress.ip_address(ip.strip())
            return True
        except ValueError:
            return False

    @staticmethod
    def is_valid_username(username: str):
        username = (username or "").strip()
        if not username:
            return False, "用户名不能为空"
        # 不能以数字开头
        if username[0].isdigit():
            return False, "用户名不能以数字开头"
        return True, ""
    @staticmethod
    def is_strong_password(password: str):
        """
            注册时判断密码强度
            1. 规定密码的长度
            2. 规定密码的组成
            """
        if len(password)<=6:
            return False,"密码长度必须大于6位"
        if not re.search(r'[a-zA-Z]',password):
            return False,"密码必须包含字母"
        if not re.search(r'\d',password):
            return False,"密码必须包含数字"
        if not re.search(r'[^a-zA-Z0-9]',password):
            return False,"密码必须包含特殊字符"
        return True,""

    #处理用户注册流程，收集注册信息，验证格式，发送给服务器
    def do_register(self):
        userName = (self.enrollUserNameEdit.text() if self.enrollUserNameEdit else "")
        password = (self.enrollUserPwdEdit.text() if self.enrollUserPwdEdit else "")
        serverIP = (self.enrollServerIPEdit.text() if self.enrollServerIPEdit else "")
        serverPort = int((self.enrollServerPortEdit.text() if self.enrollServerPortEdit else "0") or "0")

        if not self.isValidIp(serverIP):
            self.popUp("注册失败","服务器IP格式不合法", ok=False)

        ok_u, msg_u = self.is_valid_username(userName)
        if not ok_u:
            self.popUp("注册失败", msg_u, ok=False)
            return

        ok_p, msg_p = self.is_strong_password(password)
        if not ok_p:
            self.popUp("注册失败", msg_p, ok=False)
            return

        try:
            """
            创建临时socket连接发送json请求等待服务器响应，返回解析后的响应
            """
            resp = send_request(serverIP, serverPort, {
                "action": "register",
                "username": userName,
                "password": password
            })
            self.popUp("注册结果", resp.get("msg", ""), ok=resp.get("ok", False))
            if resp.get("ok"):
                self.interfaceWidget.setCurrentIndex(0)
        except Exception as e:
            self.popUp("网络错误，请检查服务器IP", str(e), ok=False)

    def do_login(self):
        username = (self.userNameEdit.text() if self.userNameEdit else "").strip()
        password = (self.userPwdEdit.text() if self.userPwdEdit else "")
        serverIP = (self.serverIPEdit.text() if self.serverIPEdit else "").strip()
        serverPort = int((self.serverPortEdit.text() if self.serverPortEdit else "0") or "0")

        if not self.isValidIp(serverIP):
            self.popUp("登录失败","服务器IP格式不合法", ok=False)
            return

        if not username or not password:
            self.popUp("登录失败", "用户名/密码不能为空", ok=False)
            return

        try:
            resp = send_request(serverIP, serverPort, {
                "action": "login",
                "username": username,
                "password": password,
                "client_ip": self.local_ip
            })
            self.popUp("登录结果", resp.get("msg", ""), ok=resp.get("ok", False))

            if resp.get("ok"):
                self.username = username
                self.interfaceWidget.setCurrentIndex(2)
                self.start_chat_connection(serverIP, serverPort)

        except Exception as e:
            self.popUp("网络错误，请检查服务器IP", str(e), ok=False)
    @staticmethod
    def toggle_password( edit: QLineEdit, btn: QToolButton, checked: bool):
        """
        checked=True  -> 显示明文
        checked=False -> 隐藏密码
        根据checked判断密码是***还是明文
        """
        if checked:
            #edit.setEchoMode()设置输入框显示模式
            #QLineEdit.Normal正常显示明文
            #QLineEdit.Password密码模式
            edit.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setIcon(QIcon(eye_open))
        else:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setIcon(QIcon(eye_close))


    # ====== 长连接 ======
    #建立长连接
    def start_chat_connection(self, server_ip, server_port):
        #先关闭已有连接
        self.close_chat_connection()
        try:
            self.sock = socket.create_connection((server_ip, server_port), timeout=5)
            self.sock.settimeout(None)
            self.stop_recv.clear()

            # 上线
            #给服务器发送上线通知，服务器收到后会通知其他用户
            send_json(self.sock, {
                "action": "online",
                "username": self.username,
                "client_ip": self.local_ip
            })
            #启动独立的接收线程，随时等待接收服务器转发的消息
            self.recv_thread = threading.Thread(target=self.recv_loop, daemon=True)
            self.recv_thread.start()

            self.emitter.info.emit(f"已连接 {server_ip}:{server_port}  本机IP={self.local_ip}")
        except Exception as e:
            self.popUp("连接失败", str(e), ok=False)

    def close_chat_connection(self):
        if self.sock is None:
            return
        try:
            self.stop_recv.set()#设置停止标志
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)#关闭
                except(OSError, ConnectionError):
                    pass
            self.sock.close()#释放资源
        finally:
            self.sock = None

    def recv_loop(self):
        #创建缓冲区，存储不完整的接收数据，解决TCP粘包问题
        buf = bytearray()
        try:
            while not self.stop_recv.is_set():
                with self.sock:
                    line = recv_line(self.sock, buf)
                if line is None:
                    break

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                #获取action字段，决定如何处理这条消息
                action = msg.get("action")

                    #接收服务器消息
                    #需要完成：
                    #    根据不同action分发处理数据，具体的action类型在server.py中有

                    #提示：收到的文件要有默认存储路径，且可以做到双击打开
                #1.接收用户列表
                if action == "user_list":
                    users=msg.get("users",[])
                    #更新左侧用户列表
                    self.emitter.users_update.emit(users)
                #2.接收私聊消息
                elif action == "private_msg":
                    from_user=msg.get("from","")
                    content=msg.get("content","")
                    timestamp_str=msg.get("timestamp","")
                    try:
                        timestamp=datetime.fromisoformat(timestamp_str)
                    except ValueError:
                        timestamp=datetime.now()

                    if from_user not in self.sessions:
                        self.sessions[from_user]={
                            "messages":[],
                            "unread":0,
                            "online":True,
                            "ip":""
                        }
                        #False表示这条消息是对方发送的
                    self.sessions[from_user]["messages"].append((False, content, timestamp))

                    # 如果不是当前打开的聊天，增加未读计数
                    if self.current_peer != from_user:
                        self.sessions[from_user]["unread"] += 1
                    else:
                        # 如果是当前聊天，立即刷新显示
                        self.refresh_message_view(from_user)
                        # 触发UI更新（左侧列表显示未读标记）
                        self.emitter.msg_in.emit(from_user, self.username, content)

                    # 3.接收群聊消息
                elif action == "public_msg":
                    from_user = msg.get("from", "")
                    content = msg.get("content", "")
                    timestamp_str = msg.get("timestamp", "")

                    try:
                        timestamp=datetime.fromisoformat(timestamp_str)
                    except ValueError:
                        timestamp=datetime.now()
                    display_content=f"{from_user}:{content}"
                    self.sessions["__ALL__"]["messages"].append((False,display_content,timestamp))
                    if self.current_peer!="__ALL__":
                        self.sessions["__ALL__"]["unread"]+=1
                    #如果是当前的公聊窗口，立即刷新
                    else:
                        self.refresh_message_view("__ALL__")
                    self.emitter.msg_in.emit(from_user,"__ALL__",content)
                #4.用户上线通知
                elif action == "user_online":
                    username=msg.get("username","")
                    ip=msg.get("ip","")
                #如果用户在会话列表中直接过呢更新用户状态
                    if username in self.sessions:
                        self.sessions[username]["online"]=True
                        self.sessions[username]["ip"]=ip
                    else:
                        #新用户创建会话
                        self.sessions[username]={
                            "messages":[],
                            "unread":0,
                            "online":True,
                            "ip":ip
                        }
                    users_list=self.build_users_list()
                    self.on_users_update(users_list)
                    self.emitter.info.emit(f"{username}上线了")
                elif action == "user_offline":
                    username=msg.get("username","")
                    if username in self.sessions:
                        self.sessions[username]["online"]=False
                    users_list=self.build_users_list()
                    self.on_users_update(users_list)
                    self.emitter.info.emit(f"{username}下线了")
                elif action == "file_transfer":
                    from_user=msg.get("from","")
                    filename=msg.get("filename","")
                    file_size=msg.get("size",0)
                    save_dir=os.path.join(os.getcwd(),"received_files")
                    os.makedirs(save_dir,exist_ok=True)
                    # 生成唯一文件名（避免重名）
                    base_name = os.path.basename(filename)
                    name, ext = os.path.splitext(base_name)
                    save_path = os.path.join(save_dir, f"{from_user}_{name}_{int(datetime.now().timestamp())}{ext}")

                    # 接收文件内容
                    try:
                        # 先回复确认，准备接收文件
                        send_json(self.sock, {"ok": True, "msg": "ready to receive file"})

                        # 接收文件数据
                        with open(save_path, "wb") as f:
                            received = 0
                            while received < file_size:

                                chunk_size = min(4096, file_size - received)
                                chunk = recv_exact(self.sock, chunk_size)
                                f.write(chunk)
                                received += len(chunk)

                        # 构造文件消息
                        file_msg = {
                            "type": "file",
                            "filename": filename,
                            "path": save_path,
                            "is_me": False,
                            "time": datetime.now(),
                            "from": from_user
                        }

                        # 存储到会话
                        if from_user not in self.sessions:
                            self.sessions[from_user] = {
                                "messages": [],
                                "unread": 0,
                                "online": True,
                                "ip": ""
                            }

                        self.sessions[from_user]["messages"].append(file_msg)

                        # 如果不是当前聊天，增加未读
                        if self.current_peer != from_user:
                            self.sessions[from_user]["unread"] += 1
                        else:
                            self.refresh_message_view(from_user)
                        self.emitter.msg_in.emit(from_user,self.username,file_msg)
                        self.emitter.info.emit(f"收到文件:{filename}")
                    except Exception as e:
                        self.emitter.info.emit(f"接收文件失败:{e}")
                elif action == "file_notify":
                    from_user = msg.get("from","")
                    filename=msg.get("filename","")
                    file_size=msg.get("size",0)
                   # file_id=msg.get("file_id","")
                    file_notice=f"[文件]{from_user} 分享了文件:{filename}({file_size}bytes)"
                    self.sessions["__ALL__"]["messages"].append((False,file_notice,datetime.now()))
                    if self.current_peer != "__ALL__":
                        self.sessions["__ALL__"]["unread"]+=1
                    else:
                        self.refresh_message_view("__ALL__")
                    self.emitter.msg_in.emit(from_user,"__ALL__",file_notice)
                elif action == "error":
                    error_msg=msg.get("msg","未知错误")
                    self.emitter.info.emit(f"服务器错误:{error_msg}")
                else:
                    print(f"未知的action：{action},消息内容:{msg}")

        except Exception as e:
            err = str(e)
            # 服务器关闭或连接断开属于正常行为
            if self.server_closed:
                pass
            elif "10054" in err:
                self.emitter.info.emit(
                    "服务器已断连"
                )
            else:
                self.emitter.info.emit(
                    f"连接异常：{e}"
                )
        finally:
            self.close_chat_connection()

    # ====== 用户列表刷新 ======
    def on_users_update(self, users: list):
        # users: [{"username":"alice","online":True,"ip":"x.x.x.x"}, ...]
        # 让所有用户都存在于 sessions（离线也保留会话）
        """
            更新左侧用户列表
            需要完成：
            1. 根据users更新sessions状态
            2. 刷新UI列表，根据登陆后server返回的用户列表更新状态
            3. 显示未读消息数
            4. 选中时要高亮
            """
        if users is not None:
            current_users={u["username"]: u for u in users}

            for username,session in self.sessions.items():
                if username != "__ALL__":
                    if username in current_users:
                        session["online"]=True
                        session["ip"]=current_users[username].get("ip","")
                    else:
                        session["online"]=False
            for user in users:
                username=user["username"]
                if username != self.username and username not in self.sessions:
                    self.sessions[username]={
                        "messages":[],
                        "unread":0,
                        "online":user.get("online",True),
                        "ip":user.get("ip","")
                    }
        if not self.chatListWidget:
            return
        #需要去保存当前选中的用户
        current_selected=None
        if self.chatListWidget.currentItem():
            display_text=self.chatListWidget.currentItem().text()
            if "公聊大厅" in display_text:
                current_selected="__ALL__"
            else:
                username=display_text
                if username.startswith("[在线]") or username.startswith("[离线]"):
                    username=username[5:].strip()
                if "(" in username:
                    username=username.split("(")[0]
                current_selected=username
        self.chatListWidget.clear()

        #添加公聊会话
        unread_all=self.sessions.get("__ALL__",{}).get("unread",0)
        if unread_all > 0:
            all_text=f"公聊大厅({unread_all})"
        else:
            all_text="公聊大厅"
        all_item=QListWidgetItem(all_text)
        self.chatListWidget.addItem(all_item)

        online_users=[]
        offline_users=[]

        for username,session in self.sessions.items():
            if username == "__ALL__" or username == self.username:
                continue

            if session.get("online",False):
                online_users.append(username)
            else:
                offline_users.append(username)

        # 添加在线用户
        for username in sorted(online_users):
            session=self.sessions[username]
            unread=session.get("unread",0)
            if unread>0:
                display_text=f"[在线]{username}({unread})"
            else:
                display_text=f"[在线]{username}"
            item=QListWidgetItem(display_text)
            self.chatListWidget.addItem(item)
            if current_selected == username:
                item.setSelected(True)
        for username in sorted(offline_users):
            display_text=f"[离线]{username}"
            item=QListWidgetItem(display_text)
            self.chatListWidget.addItem(item)
            if current_selected == username:
                item.setSelected(True)
        if current_selected == "__ALL__":
            all_item.setSelected(True)
        print("TODO: 更新用户列表")

    def on_peer_clicked(self, item):
        """
            选中发送消息的对象
            需要完成：
            1. 鼠标点击时可以选中发送对象
            2. 调用函数，左侧的用户气泡要像微信一样在底部显示消息
            """
        if item is None:
            return
        print("TODO: 选中发送消息的对象")
        display_text=item.text()#获取点击的项目文本
        if "公聊大厅" in display_text:
            self.current_peer="__ALL__"
        else:
            username=display_text
            if username.startswith("[在线]") or username.startswith("[离线]"):
                username=username[5:] #去掉前面5个字符
            if "(" in username:
                username=username.split("(")[0] #去掉未读数字部分
            self.current_peer=username.strip()
            if self.current_peer in self.sessions:
                self.sessions[self.current_peer]["unread"]=0

        self.refresh_message_view(self.current_peer)
        self.on_users_update([])

        #self.current_peer 当前选中的聊天对象
        #self.sessions 会话管理器字典

    # ====== 消息气泡显示 ======
    def refresh_message_view(self, peer: str):
        if self.messageListWidget is None:
            return

        self.messageListWidget.clear()
        self.last_msg_time = None

        msgs = self.sessions.get(peer, {}).get("messages", [])

        for m in msgs:
            self.add_message_bubble(m)

    def add_message_bubble(self, msg):
        item = QListWidgetItem()
        item.setFlags(item.flags() ^ Qt.ItemFlag.ItemIsSelectable)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 2, 10, 2)

        # ===== 文件消息 =====
        if isinstance(msg, dict) and msg.get("type") == "file":
            filename = msg["filename"]
            path = msg["path"]
            is_me = msg["is_me"]
            msg_time = msg["time"]
            from_ = msg["from"]

            bubble = QLabel(f"{from_}:\n📄 {filename}\n双击打开")
            bubble.setWordWrap(True)
            bubble.setStyleSheet("""
                        QLabel{
                            padding:10px;
                            border-radius:10px;
                            background:#E8F0FE;
                            border:1px solid #C3D3F5;
                        }
                    """)

            item.setData(Qt.ItemDataRole.UserRole,path)

        # ===== 普通文本消息 =====
        else:
            is_me, text, msg_time = msg

            bubble = QLabel(text)
            bubble.setWordWrap(True)
            bubble.setMaximumWidth(360)
            bubble.setStyleSheet("""
                        QLabel{
                            padding:8px 10px;
                            border-radius:10px;
                            background:#DCF8C6;
                        }
                    """ if is_me else """
                        QLabel{
                            padding:8px 10px;
                            border-radius:10px;
                            background:#FFFFFF;
                            border:1px solid #E5E5E5;
                        }
                    """)

        # ===== 是否显示时间 =====
        if self.last_msg_time is None or (msg_time - self.last_msg_time).total_seconds() >= 120:
            self.add_time_item(msg_time.strftime("%H:%M"))
            self.last_msg_time = msg_time

        # ===== 左右对齐 =====
        if is_me:
            layout.addStretch()
            layout.addWidget(bubble)
        else:
            layout.addWidget(bubble)
            layout.addStretch()

        self.messageListWidget.addItem(item)
        self.messageListWidget.setItemWidget(item, container)
        item.setSizeHint(container.sizeHint())
        self.messageListWidget.scrollToBottom()

    def build_users_list(self):
        """构建用户列表"""
        users = []
        for username, session in self.sessions.items():
            if username != "__ALL__" and username != self.username:
                users.append({
                    "username": username,
                    "online": session.get("online", False),
                    "ip": session.get("ip", "")
                })
        return users

    # ====== 显示消息 ======
    def on_message_in(self, _from: str, _to: str, data):
        """
            处理收到的消息，显示消息
            需要完成：
            1. 判断是群聊还是私聊
            2. 判断是文件消息还是普通消息
            3. 如果是文件消息要构造需要的msg
            4. 存入sessions
            5. 更新聊天窗口，显示消息
            6. 调用函数，左侧的用户气泡要像微信一样在底部显示消息

            提示：这里要注意区分是send_chat_message函数调用的还是recv_loop函数调用的
                注意显示的消息在聊天框的左右
            """
        #peer是当前方法内定义的一个局部变量，用来存储这条消息属于哪一个会话聊天
        if _to=="__ALL__":
            peer="__ALL__"
        elif _to==self.username:
            peer=_from
        else:
            peer=_to

        if isinstance(data, dict) and data.get("type") == "file":
            if peer==self.current_peer:
                self.refresh_message_view(peer)
        #如果不存在，创建新会话
        if peer not in self.sessions:
            self.sessions[peer]={
                "messages": [],
                "unread":0,
                "online":True,
                "ip":""
            }

        #判断消息方向
        #is_me=(_from==self.username)
        #is_me=True 我自己发的消息，右侧气泡，绿色
        #self.sessions[peer]["messages"].append((is_me,data,datetime.now()))
        #更新ui和未读数
        if peer==self.current_peer:
            self.refresh_message_view(peer)
        else:
            self.sessions[peer]["unread"]+=1
            self.on_peer_clicked(None)


        print("TODO: 实现消息处理逻辑")


    # ====== 发送消息 ======
    def send_chat_message(self):
        """
            发送聊天消息到服务器
            需要完成：
            1. 是否链接到服务器，是否选择了用户
            2. 获取输入框内容
            3. 判断是群聊还是私聊
            4. 构造JSON数据
            5. 使用socket发送
            6. 本地显示消息
            7. 调用函数，左侧的用户气泡要像微信一样在底部显示消息
        """
        #网络连接
        if not self.sock:
            self.popUp("提示","未连接到服务器",ok=False)
            return
        #选中对象
        if not self.current_peer:
            self.popUp("提示","请选择聊天对象",ok=False)
            return
        content=self.inputTextEdit.toPlainText().strip()
        if not content:
            return
        self.inputTextEdit.clear()
        #获取当前时间
        timestamp=datetime.now()
        if self.current_peer=="__ALL__":
            req={
                "action":"public_msg",
                "from":self.username,
                "content":content,
                "timestamp":timestamp.isoformat()

            }
            #本地立即显示
            self.sessions["__ALL__"]["messages"].append((True,content,timestamp))
            self.refresh_message_view("__ALL__")
        else:
            req={
                "action":"private_msg",
                "from":self.username,
                "to":self.current_peer,
                "content":content,
                "timestamp":timestamp.isoformat()
            }
            if self.current_peer not in self.sessions:
                self.sessions[self.current_peer]={
                    "messages": [],
                    "unread":0,
                    "online":True,
                    "ip":""
                }
            self.sessions[self.current_peer]["messages"].append((True,content,timestamp))
            self.refresh_message_view(self.current_peer)
            #发送给服务器
        try:
            send_json(self.sock,req)
        except Exception as e:
            self.popUp("发送失败",str(e),ok=False)


        print("TODO: 实现发送消息逻辑")

    def send_file(self):
        if not self.sock:
            self.popUp("提示", "未连接到服务器", ok=False)
            return

        if not self.current_peer:
            self.popUp("提示", "请先选择聊天对象", ok=False)
            return

        file_path, _ = QFileDialog.getOpenFileName(self.window, "选择文件")
        if not file_path:
            return

        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)

        # ===== 构造请求 =====
        if self.current_peer == "__ALL__":
            req = {
                "action": "file_notify",
                "from": self.username,
                "to": "__ALL__",
                "filename": filename,
                "size": file_size
            }
        else:
            req = {
                "action": "file_transfer",
                "from": self.username,
                "to": self.current_peer,
                "filename": filename,
                "size": file_size
            }

        # ===== 本地显示 =====
        file_msg = {
            "type": "file",
            "filename": filename,
            "path": file_path,
            "is_me": True,
            "time": datetime.now(),
            "from": self.username
        }

        if self.current_peer not in self.sessions:
            self.sessions[self.current_peer] = {
                "messages": [],
                "unread": 0,
                "online": True,
                "ip": ""
            }

        self.sessions[self.current_peer]["messages"].append(file_msg)
        self.refresh_message_view(self.current_peer)

        # ===== 发送 =====
        try:
           with self.sock_lock:
                send_json(self.sock, req)
                buf = bytearray()
                response = recv_line(self.sock, buf)

                if not response:
                    self.popUp("发送失败", "服务器无响应", ok=False)
                    return

                resp = json.loads(response)

                if not resp.get("ok"):
                    self.popUp("发送失败", resp.get("msg", "发送失败"), ok=False)
                    return

                # ===== 开始发送文件 =====
                with open(file_path, "rb") as f:
                    while True:
                        chunk = f.read(4096)
                        if not chunk:
                            break
                        self.sock.sendall(chunk)
           self.emitter.info.emit(f"文件 {filename} 发送完成")

        except Exception as e:
            self.popUp("发送失败", str(e), ok=False)

        finally:
           pass
    def open_file(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path is None:
            return
        if path and os.path.exists(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            QMessageBox.warning(self.window, "无法打开", "文件不存在或已被删除")

    def add_time_item(self, time_text):
        item = QListWidgetItem()

        label = QLabel(time_text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("""
            QLabel {
                color: #888;
                background: transparent;
                padding: 4px;
            }
        """)

        item.setSizeHint(label.sizeHint())
        self.messageListWidget.addItem(item)
        self.messageListWidget.setItemWidget(item, label)

app = QApplication(sys.argv)
w = MainWindow()
w.window.show()
sys.exit(app.exec())

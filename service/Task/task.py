# coding=utf-8
import json
import Kit
import pymysql
import requests
from flask import abort
from flask import jsonify
from flask import request
from Task import task_blue
from requests import utils
from flask import current_app as app


@task_blue.route('/sign')
@task_blue.route('/sign/<string:check_time>')
def check_list(check_time=None):
    if check_time is None:
        time_now = Kit.str_time("%H:%M")
    else:
        unix_time = Kit.timestamp2unix("2020-01-01 " + check_time + ":00")
        time_now = Kit.unix2timestamp(unix_time, pattern="%H:%M")
    app.logger.info("Check time point at {}".format(time_now))
    conn = app.mysql_pool.connection()
    # 定时重置数据库连接
    if Kit.unix_time() % 300 == 0:
        app.mysql_conn.close()
        app.mysql_conn = conn
    # 查询当前时间需要打开的用户
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    sql = "SELECT * FROM `user` WHERE `time`=%s"
    cursor.execute(query=sql, args=[time_now])
    user_task_list = cursor.fetchall()
    # 提交用户打卡任务到进程池
    for user_info in user_task_list:
        app.executor.submit(user_sign_in, app.mysql_conn, user_info, app.config["BASE"]["sms_token"])
    app.logger.info("Check point {} with {} task".format(time_now, len(user_task_list)))

    return jsonify({
        "status": "success",
        "message": "Check time: {}".format(time_now)
    })


def user_sign_in(conn, user_info, sms_token):
    # 初始化连接
    session = requests.Session()
    cookies = json.loads(user_info["cookies"])
    cookies_jar = requests.utils.cookiejar_from_dict(cookies)
    session.cookies = cookies_jar
    # 执行签到
    status, data, run_err = Kit.user_clock(session)
    # 检查更新
    session_cookies = session.cookies.get_dict()
    if session_cookies != cookies and len(session_cookies.keys()) != 0:
        app.logger.info("User {} cookies update".format(user_info["username"]))
        new_cookies = json.dumps(session_cookies)
        cursor = conn.cursor()
        sql = "UPDATE `user` SET `cookies` = %s WHERE `username` = %s"
        cursor.execute(query=sql, args=[new_cookies, user_info["username"]])

    if user_info["sms"] == "Yes":
        Kit.send_sms_message(sms_token, user_info["nickname"], user_info["phone"], str(data))
    Kit.write_log(conn, 'user_check', user_info["username"], status, data, run_err)


@task_blue.route('/count')
def check_count():
    # 本地数据校验
    client_ip = request.headers.get("X-Real-IP", "0.0.0.0")
    if client_ip != "127.0.0.1":
        return abort(400, "Reject IP:{}".format(client_ip))

    # 计算统计时间
    now_hour = Kit.unix_time()
    last_hour = now_hour - 3600
    now_hour = Kit.unix2timestamp(now_hour, "%Y-%m-%d %H:00:00")
    last_hour = Kit.unix2timestamp(last_hour, "%Y-%m-%d %H:00:00")
    print("Check log between {} and {}".format(last_hour, now_hour))

    # 查询历史数据
    conn = app.mysql_pool.connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    sql = "SELECT * FROM `log` WHERE `time` BETWEEN %s AND %s "
    cursor.execute(query=sql, args=[last_hour, now_hour])
    log_data = cursor.fetchall()
    sql = "SELECT COUNT(*) as user_num FROM `user`"
    cursor.execute(query=sql)
    user_num = int(cursor.fetchone()["user_num"])

    # 统计日志信息
    sign_count = 0
    for log in log_data:
        if log["function"] == "user_check" and log["message"] in ["操作成功", "今天已经填报了"]:
            sign_count += 1

    # 写入统计信息
    date = Kit.unix2timestamp(Kit.unix_time())
    time_range = "{}-{}".format(last_hour[11:13], now_hour[11:13])
    sql = "INSERT INTO `count`(`date`, `range`, `user_num`, `sign_num`) VALUES(%s,%s,%s,%s)"
    cursor.execute(query=sql, args=[date, time_range, user_num, sign_count])
    conn.commit()

    return "Done"
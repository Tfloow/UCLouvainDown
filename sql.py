import sqlite3
import datetime as dt

def convert_to_table_name(service_name):
    table_name = service_name.replace('.', '_')
    table_name = table_name.replace('-', '_')
    return table_name

def get_latest_status(service_name, amount=12*12):
    conn = sqlite3.connect('data/outage.sqlite3')
    cursor = conn.cursor()
    table_name = convert_to_table_name(service_name)

    cursor.execute(f'''
        SELECT * FROM {table_name} WHERE user = 0 ORDER BY timestamp DESC LIMIT {amount}
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    # convert the unix timestamp to human-readable time
    time = [dt.datetime.fromtimestamp(row[0]).strftime('%H:%M:%S') for row in rows]
    status = [row[1] for row in rows]
    
    return time[::-1],status[::-1]

def get_latest_user_report(service_name, amount=100):
    # will only take the latest 100 reports that happened in less than 24 hours
    conn = sqlite3.connect('data/outage.sqlite3')
    cursor = conn.cursor()
    table_name = convert_to_table_name(service_name)
    
    limit_unix = int((dt.datetime.now() - dt.timedelta(days=1)).timestamp())
    
    cursor.execute(f'''
        SELECT * FROM {table_name} WHERE user = 1 AND timestamp > {limit_unix} ORDER BY timestamp DESC LIMIT {amount}
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    time = [dt.datetime.fromtimestamp(row[0]).strftime('%H:%M:%S') for row in rows]
    status = [row[1] for row in rows]
    
    return time[::-1],status[::-1]

def get_percentage_uptime(service_name):
    conn = sqlite3.connect('data/outage.sqlite3')
    cursor = conn.cursor()
    table_name = convert_to_table_name(service_name)
    
    cursor.execute(f'''
        SELECT COUNT(*) FROM {table_name} WHERE status = 1 AND user = 0
    ''')
    up = cursor.fetchone()[0]
    
    cursor.execute(f'''
        SELECT COUNT(*) FROM {table_name} WHERE user = 0
    ''')
    total = cursor.fetchone()[0]
    
    conn.close()
    
    return up/total

if __name__ == "__main__":
    print(get_latest_status("ADE"))
    print(get_latest_user_report("ADE"))
    print(get_percentage_uptime("ADE"))
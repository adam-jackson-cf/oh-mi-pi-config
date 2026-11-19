import socket


def test_price_service_optional():
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=1).close()
    except Exception:
        pass

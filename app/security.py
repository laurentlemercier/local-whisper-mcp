import ipaddress,socket
from urllib.parse import urlparse
from fastapi import Header,HTTPException
from .config import *
def require_api_token(authorization:str|None=Header(default=None)):
    if API_TOKEN and authorization!=f"Bearer {API_TOKEN}": raise HTTPException(401,"Invalid or missing API token")
def _ips(host):
    try: return {ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host,None,type=socket.SOCK_STREAM)}
    except socket.gaierror as e: raise ValueError(f"Unable to resolve host: {host}") from e
def validate_remote_url(url,allow_private=False,allowed_hosts=None):
    p=urlparse(url); host=(p.hostname or "").lower().rstrip(".")
    if p.scheme not in {"http","https"}: raise ValueError("Only HTTP/HTTPS URLs are allowed")
    if p.username or p.password: raise ValueError("URL credentials are not allowed")
    if not host: raise ValueError("URL has no hostname")
    if allowed_hosts and host not in allowed_hosts: raise ValueError(f"Host is not in allow-list: {host}")
    if not allow_private and host in {"localhost","localhost.localdomain"}: raise ValueError("Localhost is not allowed")
    if not allow_private:
        for ip in _ips(host):
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified: raise ValueError(f"URL resolves to private/reserved address: {ip}")
def validate_source_url(url): validate_remote_url(url,ALLOW_PRIVATE_URLS,ALLOWED_URL_HOSTS)
def validate_callback_url(url): validate_remote_url(url,ALLOW_PRIVATE_CALLBACKS,ALLOWED_CALLBACK_HOSTS)

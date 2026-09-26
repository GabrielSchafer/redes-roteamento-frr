"""Definicao unica da topologia. Usada por gerar_configs.py e experimento.py."""

LAB = "redes"
IMAGEM_FRR = "quay.io/frrouting/frr:10.7.1"
IMAGEM_HOST = "nicolaka/netshoot:latest"

# roteador -> numero do AS
ROTEADORES = {"r1": 65001, "r2": 65001, "r3": 65002, "r4": 65003, "r5": 65003}
HOSTS = ["h1", "h2", "h3", "h4", "h5"]

# (roteador A, interface A, roteador B, interface B, prefixo /30)
# lado A recebe .1 e lado B recebe .2
ENLACES = [
    ("r1", "eth1", "r2", "eth1", "10.0.12"),
    ("r1", "eth2", "r3", "eth1", "10.0.13"),
    ("r3", "eth2", "r4", "eth1", "10.0.34"),
    ("r4", "eth2", "r5", "eth1", "10.0.45"),
    ("r2", "eth2", "r5", "eth2", "10.0.25"),
]

# Enlaces "de longa distancia": recebem atraso via netem e custo OSPF alto.
# Isso faz cada protocolo escolher um caminho diferente de h1 ate h4.
ENLACES_LENTOS = {frozenset(("r1", "r3")), frozenset(("r3", "r4"))}
ATRASO_LENTO_MS = 10          # por sentido, em cada ponta
CUSTO_OSPF_LENTO = 100
CUSTO_OSPF_NORMAL = 10

LAN_IF = "eth3"               # interface do roteador para a rede de acesso

# Timers (valores padrao, declarados explicitamente)
RIP_TIMERS = (30, 180, 120)   # update, timeout, garbage
OSPF_HELLO, OSPF_DEAD = 10, 40
BGP_KEEPALIVE, BGP_HOLD = 60, 180
BGP_CONNECT = 10

FILTRO_CONTROLE = "udp port 520 or ip proto 89 or tcp port 179"


def num(no):
    return int(no[1:])


def lan_rede(r):
    return f"192.168.{num(r)}.0/24"


def lan_gw(r):
    return f"192.168.{num(r)}.1"


def host_ip(h):
    return f"192.168.{num(h)}.10"


def loopback(r):
    return f"10.255.0.{num(r)}"


def interfaces(r):
    """Enlaces entre roteadores de r: lista de dicts."""
    out = []
    for a, ia, b, ib, pfx in ENLACES:
        lento = frozenset((a, b)) in ENLACES_LENTOS
        if r == a:
            out.append(dict(ifname=ia, ip=f"{pfx}.1", peer=b, peer_if=ib, peer_ip=f"{pfx}.2", lento=lento))
        elif r == b:
            out.append(dict(ifname=ib, ip=f"{pfx}.2", peer=a, peer_if=ia, peer_ip=f"{pfx}.1", lento=lento))
    return sorted(out, key=lambda d: d["ifname"])


def enlace_entre(a, b):
    """Retorna (if_em_a, if_em_b, lento) do enlace entre a e b."""
    for i in interfaces(a):
        if i["peer"] == b:
            return i["ifname"], i["peer_if"], i["lento"]
    raise ValueError(f"Nao existe enlace entre {a} e {b}")

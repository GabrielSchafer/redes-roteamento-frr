#!/usr/bin/env python3
"""Gera as configuracoes FRR dos tres cenarios (rip, ospf, bgp) e o topologia.clab.yml.

Uso: python3 gerar_configs.py
"""
import json
import re
from pathlib import Path

from topologia import (
    ATRASO_LENTO_MS, BGP_CONNECT, BGP_HOLD, BGP_KEEPALIVE, CUSTO_OSPF_LENTO, CUSTO_OSPF_NORMAL,
    HOSTS, IMAGEM_FRR, IMAGEM_HOST, LAB, LAN_IF, OSPF_DEAD, OSPF_HELLO, RIP_TIMERS, ROTEADORES,
    ENLACES, interfaces, lan_gw, lan_rede, loopback, host_ip, num,
)

BASE = Path(__file__).resolve().parent
CONFIGS = BASE / "configs"

DAEMONS_TODOS = ["bgpd", "ospfd", "ospf6d", "ripd", "ripngd", "isisd", "pimd", "ldpd", "nhrpd",
                 "eigrpd", "babeld", "sharpd", "pbrd", "bfdd", "fabricd", "vrrpd", "pathd"]


def daemons(ativo):
    linhas = [f"{d}={'yes' if d == ativo else 'no'}" for d in DAEMONS_TODOS]
    linhas += [
        "",
        "vtysh_enable=yes",
        'zebra_options="  -A 127.0.0.1 -s 90000000"',
        'mgmtd_options="  -A 127.0.0.1"',
        'bgpd_options="   -A 127.0.0.1"',
        'ospfd_options="  -A 127.0.0.1"',
        'ripd_options="   -A 127.0.0.1"',
        'staticd_options="-A 127.0.0.1"',
        "",
    ]
    return "\n".join(linhas)


def bloco_interfaces(r, extra_por_if):
    """Blocos 'interface' com enderecamento + linhas extras do protocolo."""
    base, proto = [], []
    blocos = [("lo", f"{loopback(r)}/32", "loopback"),
              *[(i["ifname"], f"{i['ip']}/30", f"enlace-{i['peer']}") for i in interfaces(r)],
              (LAN_IF, f"{lan_gw(r)}/24", f"lan-h{num(r)}")]
    out = []
    for ifname, ip, desc in blocos:
        out.append(f"interface {ifname}")
        if ifname != "lo":
            out.append(f" description {desc}")
        out.append(f" ip address {ip}")
        extras = extra_por_if.get(ifname, [])
        out += [f" {e}" for e in extras]
        proto += extras
        out += ["exit", "!"]
    return out, proto


def cabecalho(r):
    return ["frr defaults traditional", f"hostname {r}", "service integrated-vtysh-config", "!"]


def conf_rip(r):
    ifs, _ = bloco_interfaces(r, {})
    u, t, g = RIP_TIMERS
    proto = ["router rip", " version 2", f" timers basic {u} {t} {g}"]
    for i in interfaces(r):
        proto.append(f" network {i['ifname']}")
    proto += [f" network {LAN_IF}", " network lo", f" passive-interface {LAN_IF}", " passive-interface lo"]
    proto += ["exit", "!"]
    return cabecalho(r) + ifs + proto, proto


def conf_ospf(r):
    extra = {"lo": ["ip ospf area 0", "ip ospf passive"],
             LAN_IF: ["ip ospf area 0", "ip ospf passive"]}
    for i in interfaces(r):
        custo = CUSTO_OSPF_LENTO if i["lento"] else CUSTO_OSPF_NORMAL
        extra[i["ifname"]] = ["ip ospf area 0", "ip ospf network point-to-point",
                              f"ip ospf hello-interval {OSPF_HELLO}", f"ip ospf dead-interval {OSPF_DEAD}",
                              f"ip ospf cost {custo}"]
    ifs, proto_if = bloco_interfaces(r, extra)
    proto = ["router ospf", f" ospf router-id {loopback(r)}", "exit", "!"]
    return cabecalho(r) + ifs + proto, proto_if + proto


def conf_bgp(r):
    ifs, _ = bloco_interfaces(r, {})
    asn = ROTEADORES[r]
    proto = [f"router bgp {asn}", f" bgp router-id {loopback(r)}", " no bgp ebgp-requires-policy",
             f" timers bgp {BGP_KEEPALIVE} {BGP_HOLD}"]
    vizinhos = []
    for i in interfaces(r):
        peer_as = ROTEADORES[i["peer"]]
        tipo = "iBGP" if peer_as == asn else "eBGP"
        proto += [f" neighbor {i['peer_ip']} remote-as {peer_as}",
                  f" neighbor {i['peer_ip']} description {tipo}-{i['peer']}",
                  f" neighbor {i['peer_ip']} timers connect {BGP_CONNECT}"]
        vizinhos.append((i["peer_ip"], tipo))
    proto += [" !", " address-family ipv4 unicast",
              f"  network {loopback(r)}/32", f"  network {lan_rede(r)}"]
    for ip, tipo in vizinhos:
        proto.append(f"  neighbor {ip} activate")
        if tipo == "iBGP":
            proto.append(f"  neighbor {ip} next-hop-self")
    proto += [" exit-address-family", "exit", "!"]
    return cabecalho(r) + ifs + proto, proto


IGNORAR = re.compile(r"^(description|timers|ip ospf (hello|dead)-interval|neighbor \S+ (description|timers))")


def conta_linhas(linhas):
    """Linhas essenciais do protocolo (ignora descricoes e timers que so repetem o padrao)."""
    n = 0
    for l in linhas:
        l = l.strip()
        if l and l not in ("!", "exit", "exit-address-family") and not IGNORAR.match(l):
            n += 1
    return n


def topologia_yaml():
    y = [f"name: {LAB}", "", "topology:", "  nodes:"]
    for r in ROTEADORES:
        y += [f"    {r}:",
              "      kind: linux",
              f"      image: {IMAGEM_FRR}",
              f"      labels: {{as: \"{ROTEADORES[r]}\"}}",
              "      sysctls:",
              "        net.ipv4.ip_forward: 1",
              "        net.ipv4.conf.all.rp_filter: 0",
              "        net.ipv4.conf.default.rp_filter: 0",
              "      binds:",
              f"        - configs/ativo/{r}.conf:/etc/frr/frr.conf",
              "        - configs/ativo/daemons:/etc/frr/daemons",
              "        - configs/vtysh.conf:/etc/frr/vtysh.conf"]
    for h in HOSTS:
        r = f"r{num(h)}"
        y += [f"    {h}:",
              "      kind: linux",
              f"      image: {IMAGEM_HOST}",
              "      cmd: sleep infinity",
              "      exec:",
              f"        - ip addr add {host_ip(h)}/24 dev eth1",
              f"        - ip route add 192.168.0.0/16 via {lan_gw(r)}",
              f"        - ip route add 10.0.0.0/8 via {lan_gw(r)}"]
    y += ["", "  links:"]
    for a, ia, b, ib, _ in ENLACES:
        y.append(f'    - endpoints: ["{a}:{ia}", "{b}:{ib}"]')
    for h in HOSTS:
        y.append(f'    - endpoints: ["r{num(h)}:{LAN_IF}", "{h}:eth1"]')
    return "\n".join(y) + "\n"


def main():
    CONFIGS.mkdir(exist_ok=True)
    (CONFIGS / "vtysh.conf").write_text("service integrated-vtysh-config\n")
    geradores = {"rip": (conf_rip, "ripd"), "ospf": (conf_ospf, "ospfd"), "bgp": (conf_bgp, "bgpd")}
    complexidade = {}
    for proto, (fn, daemon) in geradores.items():
        d = CONFIGS / proto
        d.mkdir(exist_ok=True)
        (d / "daemons").write_text(daemons(daemon))
        total = 0
        for r in ROTEADORES:
            conf, linhas_proto = fn(r)
            (d / f"{r}.conf").write_text("\n".join(conf) + "\n")
            total += conta_linhas(linhas_proto)
        complexidade[proto] = total
    (CONFIGS / "complexidade.json").write_text(json.dumps(complexidade, indent=2) + "\n")
    (BASE / "topologia.clab.yml").write_text(topologia_yaml())
    print("Configs geradas em configs/ e topologia.clab.yml")
    print("Linhas de configuracao especificas do protocolo (5 roteadores):", complexidade)


if __name__ == "__main__":
    main()

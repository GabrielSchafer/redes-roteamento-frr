#!/usr/bin/env python3
"""Orquestra os experimentos de roteamento sobre a topologia do Containerlab.

Uso (precisa de root por causa do containerlab):
  sudo python3 experimento.py rodar rip|ospf|bgp|todos [--manter] [--regime 120]
  sudo python3 experimento.py subir rip|ospf|bgp       # so sobe o cenario (bom para o video)
  sudo python3 experimento.py derrubar
  sudo python3 experimento.py falha r1 r3              # corta o enlace (netem loss 100%)
  sudo python3 experimento.py restaurar r1 r3
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from topologia import (
    ATRASO_LENTO_MS, FILTRO_CONTROLE, HOSTS, IMAGEM_HOST, LAB, ROTEADORES,
    ENLACES_LENTOS, enlace_entre, host_ip, interfaces,
)

BASE = Path(__file__).resolve().parent
TOPO = BASE / "topologia.clab.yml"
CONFIGS = BASE / "configs"
ATIVO = CONFIGS / "ativo"
RESULTADOS = BASE / "resultados"
PROTOS = ["rip", "ospf", "bgp"]

PARES_RTT = [("h1", "h4"), ("h1", "h3"), ("h1", "h5"), ("h2", "h4")]
ORIGEM_FALHA, DESTINO_FALHA = "h1", "h4"
TIMEOUT_CONVERGENCIA = 300
TIMEOUT_RECUPERACAO = 420


# ---------------------------------------------------------------- utilitarios

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sh(cmd, check=True, timeout=None, mostrar=False):
    if mostrar:
        r = subprocess.run(cmd, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise RuntimeError(f"Comando falhou: {' '.join(cmd)}")
        return ""
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"Comando falhou: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def cont(no):
    return f"clab-{LAB}-{no}"


def dexec(no, *cmd, check=True, timeout=None):
    return sh(["docker", "exec", cont(no), *cmd], check=check, timeout=timeout)


def netns_cmd(no, *cmd):
    """Roda uma ferramenta do netshoot no namespace de rede do roteador (tc, tcpdump...)."""
    return ["docker", "run", "--rm", "--net", f"container:{cont(no)}",
            "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW", IMAGEM_HOST, *cmd]


def paralelo(cmds, check=True):
    procs = [subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for c in cmds]
    res = []
    for c, p in zip(cmds, procs):
        out, err = p.communicate()
        if check and p.returncode != 0:
            raise RuntimeError(f"Comando falhou: {' '.join(c)}\n{out}\n{err}")
        res.append((p.returncode, out))
    return res


def salvar_json(caminho, dados):
    caminho.write_text(json.dumps(dados, indent=2, ensure_ascii=False) + "\n")


def devolver_dono(caminho):
    """Resultados sao criados como root; devolve para o usuario que chamou o sudo."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if not uid:
        return
    for raiz, dirs, arqs in os.walk(caminho):
        for n in [raiz, *[os.path.join(raiz, x) for x in dirs + arqs]]:
            try:
                os.chown(n, int(uid), int(gid))
            except OSError:
                pass


# ---------------------------------------------------------------- lab

def derrubar():
    log("Derrubando lab (se existir)...")
    sh(["containerlab", "destroy", "-t", str(TOPO), "--cleanup"], check=False)
    for r in ROTEADORES:
        sh(["docker", "rm", "-f", f"cap-{LAB}-{r}"], check=False)


def subir(proto):
    derrubar()
    if ATIVO.exists():
        shutil.rmtree(ATIVO)
    shutil.copytree(CONFIGS / proto, ATIVO)
    log(f"Subindo cenario {proto.upper()}...")
    sh(["containerlab", "deploy", "-t", str(TOPO)], mostrar=True)
    t = time.time()
    aplicar_atrasos()
    return t


def aplicar_atrasos():
    cmds = []
    for par in ENLACES_LENTOS:
        a, b = sorted(par)
        ia, ib, _ = enlace_entre(a, b)
        for no, ifn in ((a, ia), (b, ib)):
            cmds.append(netns_cmd(no, "tc", "qdisc", "replace", "dev", ifn, "root", "netem",
                                  "delay", f"{ATRASO_LENTO_MS}ms"))
    if all(rc == 0 for rc, _ in paralelo(cmds, check=False)):
        log(f"Atraso de {ATRASO_LENTO_MS} ms aplicado nos enlaces lentos")
    else:
        log("ATENCAO: o kernel nao tem netem (comum em WSL2 antigo). Seguindo sem atraso nos enlaces lentos.")


def _regras_drop(ifn, acao):
    return " ; ".join(f"iptables {acao} {c} {d} {ifn} -j DROP"
                      for c, d in (("INPUT", "-i"), ("OUTPUT", "-o"), ("FORWARD", "-i"), ("FORWARD", "-o")))


def falha(a, b):
    """Corta o enlace sem derrubar a interface: os protocolos precisam detectar pelos proprios timers."""
    ia, ib, _ = enlace_entre(a, b)
    res = paralelo([netns_cmd(a, "tc", "qdisc", "replace", "dev", ia, "root", "netem", "loss", "100%"),
                    netns_cmd(b, "tc", "qdisc", "replace", "dev", ib, "root", "netem", "loss", "100%")],
                   check=False)
    if all(rc == 0 for rc, _ in res):
        log(f"Enlace {a}({ia}) <-> {b}({ib}) cortado (netem, perda de 100%)")
        return
    paralelo([netns_cmd(a, "sh", "-c", _regras_drop(ia, "-I")),
              netns_cmd(b, "sh", "-c", _regras_drop(ib, "-I"))])
    log(f"Enlace {a}({ia}) <-> {b}({ib}) cortado (iptables DROP, netem indisponivel)")


def restaurar(a, b):
    ia, ib, lento = enlace_entre(a, b)
    cmds = []
    for no, ifn in ((a, ia), (b, ib)):
        if lento:
            cmds.append(netns_cmd(no, "tc", "qdisc", "replace", "dev", ifn, "root", "netem",
                                  "delay", f"{ATRASO_LENTO_MS}ms"))
        else:
            cmds.append(netns_cmd(no, "tc", "qdisc", "del", "dev", ifn, "root"))
        cmds.append(netns_cmd(no, "sh", "-c", _regras_drop(ifn, "-D") + " ; true"))
    paralelo(cmds, check=False)
    log(f"Enlace {a} <-> {b} restaurado")


# ---------------------------------------------------------------- medicoes

def todos_alcancaveis():
    cmds = []
    for h in HOSTS:
        alvos = [host_ip(d) for d in HOSTS if d != h]
        cmds.append(["docker", "exec", cont(h), "fping", "-q", "-r", "0", "-t", "500", *alvos])
    return all(rc == 0 for rc, _ in paralelo(cmds, check=False))


def alcanca(origem, destino):
    r = subprocess.run(["docker", "exec", cont(origem), "fping", "-q", "-r", "0", "-t", "500",
                        host_ip(destino)], capture_output=True)
    return r.returncode == 0


def esperar_convergencia(t0):
    log("Aguardando convergencia inicial (todos os hosts se alcancando)...")
    while time.time() - t0 < TIMEOUT_CONVERGENCIA:
        if todos_alcancaveis():
            t = time.time()
            log(f"Convergiu em {t - t0:.1f} s")
            return t
        time.sleep(0.5)
    raise RuntimeError("Nao convergiu dentro do tempo limite. Veja 'show ip route' nos roteadores.")


def _captura(r, dest, so_saida):
    nome = f"cap-{LAB}-{r}"
    sh(["docker", "rm", "-f", nome], check=False)
    direcao = ["-Q", "out"] if so_saida else []
    sh(["docker", "run", "-d", "--name", nome, "--net", f"container:{cont(r)}",
        "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW", "-v", f"{dest}:/cap", IMAGEM_HOST,
        "tcpdump", "-i", "any", *direcao, "-U", "-n", "-Z", "root",
        "-w", f"/cap/{r}.pcap", FILTRO_CONTROLE])


def iniciar_capturas(dest):
    """Captura so os pacotes ENVIADOS por cada roteador, para nao contar o mesmo pacote duas vezes."""
    dest.mkdir(parents=True, exist_ok=True)
    so_saida = True
    for r in ROTEADORES:
        _captura(r, dest, so_saida)
    time.sleep(1.5)
    rodando = sh(["docker", "inspect", "-f", "{{.State.Running}}", f"cap-{LAB}-r1"], check=False).strip()
    if rodando != "true":
        log("tcpdump nao aceitou '-Q out'; capturando nos dois sentidos (analise divide por 2)")
        so_saida = False
        for r in ROTEADORES:
            _captura(r, dest, so_saida)
    log("Capturas de trafego de controle iniciadas (tcpdump em todos os roteadores)")
    return so_saida


def parar_capturas():
    for r in ROTEADORES:
        nome = f"cap-{LAB}-{r}"
        sh(["docker", "stop", "-t", "3", nome], check=False)
        sh(["docker", "rm", "-f", nome], check=False)
    log("Capturas finalizadas")


def rotas(proto):
    """Tamanho da tabela de rotas de cada roteador (ignora rede de gerencia eth0 e rotas do kernel)."""
    res = {}
    for r in ROTEADORES:
        dados = json.loads(dexec(r, "vtysh", "-c", "show ip route json"))
        total, aprendidas = 0, 0
        for prefixo, entradas in dados.items():
            sel = [e for e in entradas if e.get("selected")]
            if not sel:
                continue
            e = sel[0]
            ifs = {nh.get("interfaceName") for nh in e.get("nexthops", [])}
            if e.get("protocol") in ("kernel", "local") or ifs == {"eth0"}:
                continue
            total += 1
            if e.get("protocol") == proto:
                aprendidas += 1
        res[r] = {"total": total, "aprendidas": aprendidas}
    return res


def estado_protocolo(proto, dest, sufixo):
    cmds = {"rip": ["show ip rip", "show ip rip status"],
            "ospf": ["show ip ospf neighbor", "show ip ospf database", "show ip ospf route"],
            "bgp": ["show bgp summary", "show ip bgp"]}[proto]
    txt = []
    for r in ROTEADORES:
        for c in ["show ip route", *cmds]:
            txt.append(f"===== {r}# {c}\n" + dexec(r, "vtysh", "-c", c, check=False))
    (dest / f"estado_{sufixo}.txt").write_text("\n".join(txt))


def traceroute(origem, destino):
    out = dexec(origem, "traceroute", "-n", "-q", "1", "-w", "1", host_ip(destino), check=False)
    saltos = re.findall(r"^\s*\d+\s+(\d+\.\d+\.\d+\.\d+)", out, re.M)
    return {"saida": out, "saltos": saltos}


def medir_rtt():
    res = {}
    for o, d in PARES_RTT:
        out = dexec(o, "ping", "-c", "20", "-i", "0.2", "-q", host_ip(d), check=False)
        m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
        perda = re.search(r"([\d.]+)% packet loss", out)
        res[f"{o}-{d}"] = {
            "min": float(m[1]) if m else None, "media": float(m[2]) if m else None,
            "max": float(m[3]) if m else None, "desvio": float(m[4]) if m else None,
            "perda_pct": float(perda[1]) if perda else None,
        }
    return res


def _mem_mib(txt):
    m = re.match(r"([\d.]+)\s*([KMG]i?B|B)", txt.strip())
    if not m:
        return None
    v, u = float(m[1]), m[2]
    return v * {"B": 1 / 2**20, "KiB": 1 / 1024, "KB": 1 / 1024, "MiB": 1, "MB": 1,
                "GiB": 1024, "GB": 1024}[u]


def amostra_recursos():
    nomes = [cont(r) for r in ROTEADORES]
    out = sh(["docker", "stats", "--no-stream", "--format", "{{json .}}", *nomes])
    amostra = {"t": time.time()}
    for linha in out.strip().splitlines():
        d = json.loads(linha)
        r = d["Name"].split("-")[-1]
        amostra[r] = {"cpu_pct": float(d["CPUPerc"].rstrip("%") or 0),
                      "mem_mib": _mem_mib(d["MemUsage"].split("/")[0])}
    return amostra


def rota_saida(roteador, destino):
    """Interface usada por 'roteador' para chegar ao host destino."""
    out = dexec(roteador, "ip", "route", "get", host_ip(destino))
    return re.search(r"dev (\S+)", out)[1]


def teste_falha(dest):
    r_origem = f"r{ORIGEM_FALHA[1:]}"
    ifn = rota_saida(r_origem, DESTINO_FALHA)
    peer = next(i["peer"] for i in interfaces(r_origem) if i["ifname"] == ifn)
    log(f"Caminho ativo {ORIGEM_FALHA}->{DESTINO_FALHA} sai de {r_origem} por {ifn} (vizinho {peer})")

    dexec(ORIGEM_FALHA, "sh", "-c", "pkill ping; rm -f /tmp/ping_falha.txt", check=False)
    sh(["docker", "exec", "-d", cont(ORIGEM_FALHA), "sh", "-c",
        f"ping -D -n -i 0.1 {host_ip(DESTINO_FALHA)} > /tmp/ping_falha.txt 2>&1"])
    time.sleep(5)

    t_falha = time.time()
    falha(r_origem, peer)

    log("Aguardando reconvergencia (pode levar alguns minutos com RIP e BGP)...")
    ok_seguidos, t_rec, ultimo_log = 0, None, time.time()
    while time.time() - t_falha < TIMEOUT_RECUPERACAO:
        time.sleep(1)
        if alcanca(ORIGEM_FALHA, DESTINO_FALHA):
            ok_seguidos += 1
            if ok_seguidos == 3:
                t_rec = time.time()
                break
        else:
            ok_seguidos = 0
        if time.time() - ultimo_log >= 30:
            ultimo_log = time.time()
            log(f"  ... {time.time() - t_falha:.0f} s desde a falha")
    if t_rec is None:
        log("ATENCAO: nao reconvergiu dentro do limite")
    time.sleep(5)
    dexec(ORIGEM_FALHA, "pkill", "ping", check=False)
    time.sleep(0.5)
    (dest / "ping_falha.txt").write_text(dexec(ORIGEM_FALHA, "cat", "/tmp/ping_falha.txt", check=False))
    return {"t_falha": t_falha, "t_recuperado_aprox": t_rec, "enlace_cortado": [r_origem, peer],
            "interface": ifn}


# ---------------------------------------------------------------- experimento

def rodar(proto, regime_s, manter):
    dest = RESULTADOS / proto
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    ev = {"protocolo": proto}

    ev["t_deploy"] = subir(proto)
    ev["captura_so_saida"] = iniciar_capturas(dest)
    ev["t_convergido"] = esperar_convergencia(ev["t_deploy"])

    log("Estabilizando por 20 s antes da janela de regime...")
    time.sleep(20)

    ev["t_regime_ini"] = time.time()
    log(f"Janela de regime: {regime_s} s (rotas, traceroute, RTT, CPU/memoria)")
    salvar_json(dest / "rotas_antes.json", rotas(proto))
    estado_protocolo(proto, dest, "antes")
    salvar_json(dest / "traceroute_antes.json", traceroute(ORIGEM_FALHA, DESTINO_FALHA))
    salvar_json(dest / "rtt.json", medir_rtt())
    amostras = []
    while time.time() - ev["t_regime_ini"] < regime_s:
        amostras.append(amostra_recursos())
        time.sleep(10)
    salvar_json(dest / "recursos.json", amostras)
    restante = regime_s - (time.time() - ev["t_regime_ini"])
    if restante > 0:
        time.sleep(restante)
    ev["t_regime_fim"] = time.time()

    ev.update(teste_falha(dest))
    ev["t_pos_falha"] = time.time()
    salvar_json(dest / "rotas_depois.json", rotas(proto))
    estado_protocolo(proto, dest, "depois")
    salvar_json(dest / "traceroute_depois.json", traceroute(ORIGEM_FALHA, DESTINO_FALHA))
    restaurar(*ev["enlace_cortado"])
    time.sleep(2)

    parar_capturas()
    ev["t_fim"] = time.time()
    salvar_json(dest / "eventos.json", ev)
    if not manter:
        derrubar()
    devolver_dono(RESULTADOS)
    log(f"Cenario {proto.upper()} concluido. Resultados em {dest}")


def main():
    if os.geteuid() != 0:
        sys.exit("Rode com sudo (o containerlab precisa de root).")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("rodar")
    p.add_argument("proto", choices=PROTOS + ["todos"])
    p.add_argument("--regime", type=int, default=120, help="duracao da janela de regime (s)")
    p.add_argument("--manter", action="store_true", help="nao derruba o lab no final")
    p = sub.add_parser("subir")
    p.add_argument("proto", choices=PROTOS)
    sub.add_parser("derrubar")
    for nome in ("falha", "restaurar"):
        p = sub.add_parser(nome)
        p.add_argument("a")
        p.add_argument("b")
    a = ap.parse_args()

    if a.cmd == "rodar":
        protos = PROTOS if a.proto == "todos" else [a.proto]
        for i, pr in enumerate(protos):
            rodar(pr, a.regime, manter=a.manter and i == len(protos) - 1)
        log("Pronto. Agora rode: python3 analisar.py")
    elif a.cmd == "subir":
        t0 = subir(a.proto)
        esperar_convergencia(t0)
    elif a.cmd == "derrubar":
        derrubar()
    elif a.cmd == "falha":
        falha(a.a, a.b)
    elif a.cmd == "restaurar":
        restaurar(a.a, a.b)


if __name__ == "__main__":
    main()

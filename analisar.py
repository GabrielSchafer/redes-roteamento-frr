#!/usr/bin/env python3
"""Le resultados/<proto>/, calcula as metricas e gera os graficos comparativos.

Uso: python3 analisar.py        (nao precisa de sudo; requer matplotlib)
Saidas: resultados/resumo.csv, resultados/resumo.md, resultados/csv/*.csv e resultados/graficos/*.png
"""
import csv
import json
import re
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = Path(__file__).resolve().parent
RES = BASE / "resultados"
GRAF = RES / "graficos"
CSV = RES / "csv"
PROTOS = ["rip", "ospf", "bgp"]
CORES = {"rip": "#E07A5F", "ospf": "#3D85C6", "bgp": "#6AA84F"}
NOMES = {"rip": "RIP", "ospf": "OSPF", "bgp": "BGP"}


# ---------------------------------------------------------------- leitura

def ler_pcap(caminho):
    """Parser minimo de pcap. Retorna [(timestamp, bytes_como_quadro_ethernet)] so de pacotes enviados."""
    if not caminho.exists():
        return []
    d = caminho.read_bytes()
    if len(d) < 24:
        return []
    magicos = {b"\xd4\xc3\xb2\xa1": ("<", 1e6), b"\xa1\xb2\xc3\xd4": (">", 1e6),
               b"\x4d\x3c\xb2\xa1": ("<", 1e9), b"\xa1\xb2\x3c\x4d": (">", 1e9)}
    end, div = magicos[d[:4]]
    linktype = struct.unpack(end + "I", d[20:24])[0]
    pos, pacotes = 24, []
    while pos + 16 <= len(d):
        seg, frac, incl, orig = struct.unpack(end + "IIII", d[pos:pos + 16])
        corpo = d[pos + 16:pos + 16 + incl]
        pos += 16 + incl
        if len(corpo) < incl:
            break
        if linktype == 113:      # Linux cooked v1: tipo do pacote nos 2 primeiros bytes
            cab, tipo = 16, struct.unpack(">H", corpo[0:2])[0]
        elif linktype == 276:    # Linux cooked v2: tipo do pacote no byte 10
            cab, tipo = 20, corpo[10]
        else:
            cab, tipo = 14, 4
        if tipo != 4:            # 4 = PACKET_OUTGOING
            continue
        pacotes.append((seg + frac / div, orig - cab + 14))
    return pacotes


def ler_ping(caminho):
    res = []
    if not caminho.exists():
        return res
    for linha in caminho.read_text(errors="ignore").splitlines():
        m = re.search(r"\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+)", linha)
        if m:
            res.append((float(m[1]), int(m[2]), float(m[3])))
    return sorted(res)


def carregar(proto):
    d = RES / proto
    if not (d / "eventos.json").exists():
        return None
    j = lambda n: json.loads((d / n).read_text()) if (d / n).exists() else None  # noqa: E731
    por_roteador = {pcap.stem: ler_pcap(pcap) for pcap in sorted(d.glob("r*.pcap"))}
    pacotes = [p for lista in por_roteador.values() for p in lista]
    return {"ev": j("eventos.json"), "rotas_antes": j("rotas_antes.json"), "rotas_depois": j("rotas_depois.json"),
            "rtt": j("rtt.json"), "recursos": j("recursos.json"), "tr_antes": j("traceroute_antes.json"),
            "tr_depois": j("traceroute_depois.json"), "ping": ler_ping(d / "ping_falha.txt"),
            "pacotes": sorted(pacotes), "pacotes_por_roteador": por_roteador}


# ---------------------------------------------------------------- metricas

def janela(pacotes, t0, t1):
    sel = [b for t, b in pacotes if t0 <= t < t1]
    return len(sel), sum(sel)


def interrupcao(ping, t_falha):
    """Maior intervalo sem respostas depois da falha."""
    melhor = None
    for (t_a, s_a, _), (t_b, s_b, _) in zip(ping, ping[1:]):
        if t_b < t_falha:
            continue
        if melhor is None or t_b - t_a > melhor[1] - melhor[0]:
            melhor = (t_a, t_b, s_b - s_a - 1)
    return melhor


def metricas(proto, x):
    ev = x["ev"]
    m = {"protocolo": NOMES[proto]}
    m["conv_inicial_s"] = ev["t_convergido"] - ev["t_deploy"]

    dur = ev["t_regime_fim"] - ev["t_regime_ini"]
    n, b = janela(x["pacotes"], ev["t_regime_ini"], ev["t_regime_fim"])
    m["pacotes_regime_por_min"] = n / dur * 60
    m["taxa_regime_bps"] = b * 8 / dur
    m["bytes_regime_por_min"] = b / dur * 60

    intr = interrupcao(x["ping"], ev["t_falha"])
    if intr:
        m["reconvergencia_s"] = intr[1] - ev["t_falha"]
        m["interrupcao_s"] = intr[1] - intr[0]
        m["pings_perdidos"] = intr[2]
        n, b = janela(x["pacotes"], ev["t_falha"], intr[1])
        m["pacotes_durante_falha"] = n
        m["bytes_durante_falha"] = b
    n, b = janela(x["pacotes"], 0, 1e12)
    m["pacotes_total"], m["bytes_total"] = n, b

    ra, rd = x["rotas_antes"], x["rotas_depois"]
    m["rotas_por_roteador"] = sum(v["total"] for v in ra.values()) / len(ra)
    m["rotas_aprendidas_por_roteador"] = sum(v["aprendidas"] for v in ra.values()) / len(ra)
    m["rotas_aprendidas_total"] = sum(v["aprendidas"] for v in ra.values())
    m["rotas_aprendidas_total_pos_falha"] = sum(v["aprendidas"] for v in rd.values()) if rd else None

    for par, v in (x["rtt"] or {}).items():
        m[f"rtt_{par}_ms"] = v["media"]

    amostras = x["recursos"] or []
    roteadores = [k for k in (amostras[0] if amostras else {}) if k != "t"]
    if roteadores:
        m["cpu_pct_por_roteador"] = sum(a[r]["cpu_pct"] for a in amostras for r in roteadores) / (len(amostras) * len(roteadores))
        m["mem_mib_por_roteador"] = sum(a[r]["mem_mib"] or 0 for a in amostras for r in roteadores) / (len(amostras) * len(roteadores))

    comp = BASE / "configs" / "complexidade.json"
    if comp.exists():
        m["linhas_config"] = json.loads(comp.read_text()).get(proto)

    m["caminho_h1_h4_antes"] = " > ".join((x["tr_antes"] or {}).get("saltos", []))
    m["caminho_h1_h4_depois"] = " > ".join((x["tr_depois"] or {}).get("saltos", []))
    m["enlace_cortado"] = "-".join(ev.get("enlace_cortado", []))
    return m


# ---------------------------------------------------------------- graficos

def barras(dados, chave, titulo, ylabel, arquivo, fmt="{:.1f}"):
    protos = [p for p in dados if dados[p].get(chave) is not None]
    if not protos:
        return
    vals = [dados[p][chave] for p in protos]
    fig, ax = plt.subplots(figsize=(6, 4))
    barras_ = ax.bar([NOMES[p] for p in protos], vals, color=[CORES[p] for p in protos])
    for r, v in zip(barras_, vals):
        ax.annotate(fmt.format(v), (r.get_x() + r.get_width() / 2, r.get_height()),
                    ha="center", va="bottom", fontsize=10, xytext=(0, 3), textcoords="offset points")
    ax.set_title(titulo)
    ax.set_ylabel(ylabel)
    ax.margins(y=0.15)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(GRAF / arquivo, dpi=150)
    plt.close(fig)


def barras_agrupadas(dados, chaves, rotulos, titulo, ylabel, arquivo, fmt="{:.1f}"):
    protos = list(dados)
    largura = 0.8 / len(protos)
    fig, ax = plt.subplots(figsize=(7.5, 4))
    for i, p in enumerate(protos):
        xs = [k + i * largura for k in range(len(chaves))]
        vals = [dados[p].get(c) or 0 for c in chaves]
        bs = ax.bar(xs, vals, largura, label=NOMES[p], color=CORES[p])
        for r, v in zip(bs, vals):
            ax.annotate(fmt.format(v), (r.get_x() + r.get_width() / 2, r.get_height()),
                        ha="center", va="bottom", fontsize=8, xytext=(0, 2), textcoords="offset points")
    ax.set_xticks([k + largura * (len(protos) - 1) / 2 for k in range(len(chaves))])
    ax.set_xticklabels(rotulos)
    ax.set_title(titulo)
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False)
    ax.margins(y=0.15)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(GRAF / arquivo, dpi=150)
    plt.close(fig)


def controle_por_segundo(x):
    """{segundo relativo a falha: [pacotes, bytes]} entre o inicio do regime e o fim da captura."""
    ev = x["ev"]
    t0 = ev["t_falha"]
    inicio, fim = int(ev["t_regime_ini"] - t0), int(ev["t_fim"] - t0) + 1
    contagem = {s: [0, 0] for s in range(inicio, fim)}
    for t, b in x["pacotes"]:
        s = int(t - t0) if t >= t0 else int(t - t0) - 1
        if s in contagem:
            contagem[s][0] += 1
            contagem[s][1] += b
    return contagem


def linha_do_tempo(brutos):
    fig, eixos = plt.subplots(len(brutos), 1, figsize=(9, 2.6 * len(brutos)), sharex=True, squeeze=False)
    fim_max = max(x["ev"]["t_fim"] - x["ev"]["t_falha"] for x in brutos.values())
    for ax, (p, x) in zip(eixos[:, 0], brutos.items()):
        ev = x["ev"]
        t0 = ev["t_falha"]
        contagem = controle_por_segundo(x)
        ax.bar(list(contagem), [v[0] for v in contagem.values()], width=1.0, color=CORES[p])
        fim = ev["t_fim"] - t0
        if fim < fim_max - 1:
            # Captura deste cenario terminou antes: sem dados, nao e silencio do protocolo
            ax.axvspan(fim, fim_max, facecolor="none", edgecolor="gray", hatch="//", lw=0)
            ax.text((fim + fim_max) / 2, 0.5, "captura encerrada", transform=ax.get_xaxis_transform(),
                    ha="center", va="center", fontsize=9, color="dimgray",
                    bbox={"facecolor": "white", "edgecolor": "none"})
        ax.axvline(0, color="black", ls="--", lw=1)
        intr = interrupcao(x["ping"], t0)
        if intr:
            ax.axvspan(0, intr[1] - t0, color="gray", alpha=0.15)
        ax.set_ylabel(f"{NOMES[p]}\npacotes/s")
        ax.spines[["top", "right"]].set_visible(False)
    eixos[0, 0].set_title("Pacotes de controle por segundo (t=0: falha; cinza: sem conectividade; hachura: sem captura)")
    eixos[-1, 0].set_xlabel("tempo relativo a falha (s)")
    fig.tight_layout()
    fig.savefig(GRAF / "10_linha_do_tempo_controle.png", dpi=150)
    plt.close(fig)


def ping_falha(brutos):
    fig, eixos = plt.subplots(len(brutos), 1, figsize=(9, 2.4 * len(brutos)), sharex=True, squeeze=False)
    for ax, (p, x) in zip(eixos[:, 0], brutos.items()):
        t0 = x["ev"]["t_falha"]
        ax.scatter([t - t0 for t, _, _ in x["ping"]], [r for _, _, r in x["ping"]], s=3, color=CORES[p])
        ax.axvline(0, color="black", ls="--", lw=1)
        ax.set_ylabel(f"{NOMES[p]}\nRTT (ms)")
        ax.spines[["top", "right"]].set_visible(False)
    eixos[0, 0].set_title("Ping h1 -> h4 a cada 100 ms durante a falha (lacuna = pacotes perdidos)")
    eixos[-1, 0].set_xlabel("tempo relativo a falha (s)")
    fig.tight_layout()
    fig.savefig(GRAF / "11_ping_durante_falha.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- csv

def gravar_csv(nome, colunas, linhas):
    with open(CSV / nome, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(colunas)
        w.writerows([round(v, 6) if isinstance(v, float) else v for v in linha] for linha in linhas)


def exportar_csvs(brutos):
    """Dados brutos de cada metrica, um arquivo por tipo, todos com a coluna protocolo."""
    CSV.mkdir(parents=True, exist_ok=True)
    eventos, pacotes, por_segundo, ping, rotas, rtt, recursos, caminhos = ([] for _ in range(8))
    for p, x in brutos.items():
        ev, nome = x["ev"], NOMES[p]
        t0 = ev["t_falha"]
        for k, v in ev.items():
            if k.startswith("t_"):
                eventos.append([nome, k[2:], v, v - t0])
        for r, lista in x["pacotes_por_roteador"].items():
            pacotes += [[nome, r, t, t - t0, b] for t, b in lista]
        por_segundo += [[nome, s, n, b] for s, (n, b) in controle_por_segundo(x).items()]
        ping += [[nome, t, t - t0, seq, ms] for t, seq, ms in x["ping"]]
        for momento, chave in (("antes", "rotas_antes"), ("depois", "rotas_depois")):
            for r, v in (x[chave] or {}).items():
                rotas.append([nome, momento, r, v["total"], v["aprendidas"]])
        for par, v in (x["rtt"] or {}).items():
            rtt.append([nome, par, v.get("min"), v.get("media"), v.get("max"), v.get("desvio"), v.get("perda_pct")])
        for a in x["recursos"] or []:
            recursos += [[nome, a["t"], r, v["cpu_pct"], v["mem_mib"]] for r, v in a.items() if r != "t"]
        for momento, chave in (("antes", "tr_antes"), ("depois", "tr_depois")):
            for i, salto in enumerate((x[chave] or {}).get("saltos", []), 1):
                caminhos.append([nome, momento, i, salto])

    gravar_csv("eventos.csv", ["protocolo", "evento", "timestamp", "t_rel_falha_s"], eventos)
    gravar_csv("pacotes_controle.csv", ["protocolo", "roteador", "timestamp", "t_rel_falha_s", "bytes"], pacotes)
    gravar_csv("controle_por_segundo.csv", ["protocolo", "segundo_rel_falha", "pacotes", "bytes"], por_segundo)
    gravar_csv("ping_falha.csv", ["protocolo", "timestamp", "t_rel_falha_s", "icmp_seq", "rtt_ms"], ping)
    gravar_csv("rotas.csv", ["protocolo", "momento", "roteador", "total", "aprendidas"], rotas)
    gravar_csv("rtt.csv", ["protocolo", "par", "min_ms", "media_ms", "max_ms", "desvio_ms", "perda_pct"], rtt)
    gravar_csv("recursos.csv", ["protocolo", "timestamp", "roteador", "cpu_pct", "mem_mib"], recursos)
    gravar_csv("caminho_h1_h4.csv", ["protocolo", "momento", "salto", "ip"], caminhos)


# ---------------------------------------------------------------- main

def main():
    brutos = {p: x for p in PROTOS if (x := carregar(p))}
    if not brutos:
        raise SystemExit("Nenhum resultado em resultados/. Rode o experimento primeiro.")
    GRAF.mkdir(parents=True, exist_ok=True)
    m = {p: metricas(p, x) for p, x in brutos.items()}

    colunas = []
    for v in m.values():
        colunas += [k for k in v if k not in colunas]
    with open(RES / "resumo.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colunas)
        w.writeheader()
        for v in m.values():
            w.writerow({k: (round(x, 3) if isinstance(x, float) else x) for k, x in v.items()})

    linhas = ["| Metrica | " + " | ".join(NOMES[p] for p in m) + " |",
              "|---|" + "---|" * len(m)]
    for c in colunas[1:]:
        vals = [m[p].get(c) for p in m]
        vals = [f"{v:.2f}" if isinstance(v, float) else ("" if v is None else str(v)) for v in vals]
        linhas.append(f"| {c} | " + " | ".join(vals) + " |")
    (RES / "resumo.md").write_text("\n".join(linhas) + "\n")
    exportar_csvs(brutos)

    barras_agrupadas(m, ["rotas_por_roteador", "rotas_aprendidas_por_roteador"],
                     ["Total na tabela", "Aprendidas pelo protocolo"],
                     "Tamanho da tabela de rotas (media por roteador)", "rotas", "01_tabela_rotas.png")
    barras(m, "pacotes_regime_por_min", "Pacotes de controle em regime (rede inteira)", "pacotes/min",
           "02_pacotes_controle.png")
    barras(m, "taxa_regime_bps", "Taxa usada pelo protocolo em regime (rede inteira)", "bit/s",
           "03_taxa_controle.png")
    barras(m, "conv_inicial_s", "Tempo de convergencia inicial", "segundos", "04_convergencia_inicial.png")
    barras(m, "reconvergencia_s", "Tempo de reconvergencia apos falha de enlace", "segundos",
           "05_reconvergencia.png")
    barras(m, "pings_perdidos", "Pings perdidos durante a falha (1 ping a cada 100 ms)", "pacotes",
           "06_pings_perdidos.png", fmt="{:.0f}")
    pares = sorted({k for v in m.values() for k in v if k.startswith("rtt_")})
    barras_agrupadas(m, pares, [p[4:-3].replace("-", " > ") for p in pares],
                     "Delay: RTT medio entre hosts (antes da falha)", "ms", "07_rtt.png")
    barras(m, "cpu_pct_por_roteador", "CPU media por roteador em regime", "% CPU", "08a_cpu.png", fmt="{:.2f}")
    barras(m, "mem_mib_por_roteador", "Memoria media por roteador", "MiB", "08b_memoria.png")
    barras(m, "linhas_config", "Complexidade: linhas de configuracao do protocolo (5 roteadores)",
           "linhas", "09_complexidade.png", fmt="{:.0f}")
    barras(m, "pacotes_durante_falha", "Pacotes de controle entre a falha e a reconvergencia", "pacotes",
           "12_pacotes_durante_falha.png", fmt="{:.0f}")
    linha_do_tempo(brutos)
    ping_falha(brutos)

    print((RES / "resumo.md").read_text())
    print(f"CSVs em {CSV}")
    print(f"Graficos em {GRAF}")


if __name__ == "__main__":
    main()

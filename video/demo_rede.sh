#!/usr/bin/env bash
# Demonstracao curta (~1 min por protocolo) no nivel da rede: enderecos, rotas no kernel,
# pacotes do protocolo decodificados no enlace e o encaminhamento de um ping.
# Uso (dentro da VM, na pasta do repositorio, com o cenario no ar):
#   bash video/demo_rede.sh           # avanca com Enter a cada etapa
#   bash video/demo_rede.sh --auto    # avanca sozinho (pausa de PAUSA segundos)
# O cenario precisa estar no ar antes: sudo python3 experimento.py subir ospf|rip|bgp

set -u
cd "$(dirname "$0")/.."

AUTO=0
PAUSA=${PAUSA:-3}
case ${1:-} in
  --auto) AUTO=1 ;;
  "") ;;
  *) echo "Opcao desconhecida: $1"; exit 1 ;;
esac

AZUL=$'\e[1;34m'; VERDE=$'\e[1;32m'; AMAR=$'\e[1;33m'; CINZA=$'\e[0;90m'; FIM=$'\e[0m'

titulo() { printf "\n%s━━━ %s ━━━%s\n" "$AZUL" "$1" "$FIM"; }
nota()   { printf "%s%s%s\n" "$CINZA" "$1" "$FIM"; }
mostra() { printf "%s%s%s\n" "$AMAR" "$1" "$FIM"; }
pausa() {
  if [ "$AUTO" = 1 ]; then sleep "$PAUSA"; else read -rsp $'\n[Enter para continuar]' _; echo; fi
}
# Roda no roteador (docker exec) e mostra o comando com o prompt do no
em() { mostra "[$1]\$ ${*:2}"; docker exec "clab-redes-$1" "${@:2}"; }
# Ferramentas do netshoot (tcpdump, ss) dentro do namespace de rede do roteador
ns() { docker run --rm --net "container:clab-redes-$1" nicolaka/netshoot "${@:2}"; }

if [ ! -f configs/ativo/daemons ]; then
  echo "Nenhum cenario no ar. Rode antes: sudo python3 experimento.py subir ospf|rip|bgp"
  exit 1
fi
PROTO=$(grep -oE '^(ripd|ospfd|bgpd)=yes' configs/ativo/daemons | cut -d= -f1 | sed 's/d$//')
case $PROTO in
  rip)  NOME=RIP;  FILTRO="udp port 520";  MEIO=r3; MEIO_IF=eth2
        MSG="RIPv2 Response: a tabela inteira anunciada ao vizinho, rede por rede, com a métrica em saltos" ;;
  ospf) NOME=OSPF; FILTRO="ip proto 89";   MEIO=r2; MEIO_IF=eth2
        MSG="OSPF Hello: timers (Hello 10 s, Dead 40 s) e a lista de vizinhos que R1 já enxerga" ;;
  bgp)  NOME=BGP;  FILTRO="tcp port 179";  MEIO=r2; MEIO_IF=eth2
        MSG="BGP UPDATE: cada prefixo anunciado com o AS_PATH e o next-hop" ;;
  *) echo "Nao consegui identificar o protocolo em configs/ativo/daemons"; exit 1 ;;
esac

clear
printf "%s%s no nível da rede%s\n" "$VERDE" "$NOME" "$FIM"
nota "Endereços, rotas no kernel, pacotes do protocolo no enlace e encaminhamento."
pausa

# ------------------------------------------------------------ 1. enderecos

titulo "1. Interfaces e endereços de R1"
em r1 ip -br -4 addr
nota "eth1 → R2 (/30), eth2 → R3 (/30), eth3 → rede do h1 (/24), lo → identidade do roteador (/32)"
pausa

# ------------------------------------------------------------ 2. rotas no kernel

titulo "2. Tabela de rotas do kernel de R1"
em r1 ip route
nota "\"proto $PROTO\" = rota aprendida pelo $NOME e instalada no kernel pelo FRR (zebra). \"proto kernel\" = redes diretamente conectadas."
pausa

# ------------------------------------------------------------ 3. protocolo no enlace

titulo "3. Mensagem do $NOME capturada no enlace R1–R2"
if [ "$PROTO" = bgp ]; then
  mostra "[r1]\$ ss -tn state established '( sport = :179 or dport = :179 )'"
  ns r1 ss -tn state established '( sport = :179 or dport = :179 )'
  nota "O BGP roda sobre TCP: uma sessão com R2 (iBGP) e outra com R3 (eBGP)."
  echo
  mostra "[r1]\$ tcpdump -l -i eth1 -n -vv -c 1 'tcp port 179 and tcp[tcpflags] & tcp-push != 0'"
  ns r1 timeout 20 tcpdump -l -i eth1 -n -vv -c 1 'tcp port 179 and tcp[tcpflags] & tcp-push != 0' 2>/dev/null &
  CAP=$!
  sleep 3
  em r1 vtysh -c "clear bgp ipv4 unicast 10.0.12.2 soft out"
  nota "Pede para R1 reenviar os anúncios a R2 sem derrubar a sessão."
  wait "$CAP"
else
  [ "$PROTO" = rip ] && nota "O RIP anuncia a cada 30 s: pode levar alguns segundos."
  [ "$PROTO" = ospf ] && nota "O OSPF manda Hello a cada 10 s: pode levar alguns segundos."
  mostra "[r1]\$ tcpdump -l -i eth1 -n -vv -c 1 '$FILTRO'"
  ns r1 timeout 40 tcpdump -l -i eth1 -n -vv -c 1 "$FILTRO" 2>/dev/null
fi
nota "$MSG"
pausa

# ------------------------------------------------------------ 4. encaminhamento

titulo "4. Encaminhamento: ping de h1 para h4 visto em ${MEIO^^}"
nota "Captura ICMP no roteador do meio do caminho escolhido pelo $NOME:"
mostra "[${MEIO}]\$ tcpdump -i $MEIO_IF -n -c 4 icmp"
ns "$MEIO" timeout 20 tcpdump -l -i "$MEIO_IF" -n -c 4 icmp 2>/dev/null &
CAP=$!
sleep 3
echo
mostra "[h1]\$ ping -c 2 192.168.4.10"
docker exec clab-redes-h1 ping -c 2 192.168.4.10 | sed -n '2,3p'
wait "$CAP"
nota "O pedido e a resposta passam por ${MEIO^^}. TTL da resposta: 64 − saltos percorridos."

printf "\n%sFim: %s no nível da rede.%s\n" "$VERDE" "$NOME" "$FIM"

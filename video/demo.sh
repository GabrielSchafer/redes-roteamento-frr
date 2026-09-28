#!/usr/bin/env bash
# Demonstracao do lab ja em pe, para gravar o video (sem narracao).
# Uso (dentro da VM, na pasta do repositorio):
#   bash video/demo.sh            # avanca com Enter a cada etapa
#   bash video/demo.sh --falha    # inclui o teste de falha ao vivo
#   bash video/demo.sh --auto     # avanca sozinho (pausa de PAUSA segundos)
# O cenario precisa estar no ar antes: sudo python3 experimento.py subir ospf|rip|bgp

set -u
cd "$(dirname "$0")/.."

FALHA=0
AUTO=0
PAUSA=${PAUSA:-4}
for a in "$@"; do
  case $a in
    --falha) FALHA=1 ;;
    --auto) AUTO=1 ;;
    *) echo "Opcao desconhecida: $a"; exit 1 ;;
  esac
done

AZUL=$'\e[1;34m'; VERDE=$'\e[1;32m'; VERM=$'\e[1;31m'; AMAR=$'\e[1;33m'; CINZA=$'\e[0;90m'; FIM=$'\e[0m'

titulo() { printf "\n%s━━━ %s ━━━%s\n" "$AZUL" "$1" "$FIM"; }
nota()   { printf "%s%s%s\n" "$CINZA" "$1" "$FIM"; }
mostra() { printf "%s\$ %s%s\n" "$AMAR" "$*" "$FIM"; }
cmd()    { mostra "$@"; "$@"; }
tc_em()  { cmd docker run --rm --net container:clab-redes-$1 --cap-add NET_ADMIN nicolaka/netshoot tc qdisc "${@:2}"; }
pausa() {
  if [ "$AUTO" = 1 ]; then sleep "$PAUSA"; else read -rsp $'\n[Enter para continuar]' _; echo; fi
}

# Traduz IPs do traceroute para o nome do roteador
nomear() {
  sed -E -e '/^ *[0-9]/!b' -e 's/(192\.168\.1\.1 )/\1 (R1)/' \
         -e 's/(10\.0\.12\.2 )/\1 (R2)/' -e 's/(10\.0\.13\.2 )/\1 (R3)/' \
         -e 's/(10\.0\.25\.2 )/\1 (R5)/' -e 's/(10\.0\.34\.2 )/\1 (R4)/' \
         -e 's/(10\.0\.45\.1 )/\1 (R4)/' -e 's/(192\.168\.4\.10 )/\1 (h4)/'
}

# ------------------------------------------------------------ verificacoes

if [ ! -f configs/ativo/daemons ]; then
  echo "Nenhum cenario no ar. Rode antes: sudo python3 experimento.py subir ospf|rip|bgp"
  exit 1
fi
PROTO=$(grep -oE '^(ripd|ospfd|bgpd)=yes' configs/ativo/daemons | cut -d= -f1 | sed 's/d$//')
case $PROTO in
  rip)  NOME=RIP;  VIZ="show ip rip status"; ROTAS="show ip route rip"; RECORTE='/Routing Information Sources/,$p' 
        CAMINHO="R1 → R3 → R4 (menos saltos, pelos enlaces lentos)"; CORTE="r1 eth2 r3 eth1"; LENTO=1 ;;
  ospf) NOME=OSPF; VIZ="show ip ospf neighbor"; ROTAS="show ip route ospf"; RECORTE='p' 
        CAMINHO="R1 → R2 → R5 → R4 (menor custo: 30 contra 200)"; CORTE="r1 eth1 r2 eth1"; LENTO=0 ;;
  bgp)  NOME=BGP;  VIZ="show bgp summary"; ROTAS="show ip route bgp"; RECORTE='/^Neighbor/,/^Total/{/^Total/!p;}' 
        CAMINHO="R1 → R2 → R5 → R4 (menor AS_PATH: 65003 contra 65002 65003)"; CORTE="r1 eth1 r2 eth1"; LENTO=0 ;;
  *) echo "Nao consegui identificar o protocolo em configs/ativo/daemons"; exit 1 ;;
esac

clear
printf "%sDemonstração: roteamento com %s (FRRouting em containers)%s\n" "$VERDE" "$NOME" "$FIM"
nota "5 roteadores em 3 AS, 5 hosts. Topologia completa no README."
pausa

# ------------------------------------------------------------ 1. containers

titulo "1. Containers do lab"
cmd docker ps --filter "name=clab-redes-" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
N=$(docker ps --filter "name=clab-redes-" --filter "status=running" -q | wc -l)
if [ "$N" -eq 10 ]; then
  printf "%s✔ %s de 10 containers rodando (5 roteadores + 5 hosts)%s\n" "$VERDE" "$N" "$FIM"
else
  printf "%s✘ Só %s de 10 containers rodando%s\n" "$VERM" "$N" "$FIM"
fi
pausa

# ------------------------------------------------------------ 2. daemons

titulo "2. Protocolo ativo nos roteadores"
nota "Só um protocolo roda por vez. Daemons do FRR em cada roteador:"
for r in r1 r2 r3 r4 r5; do
  cmd docker exec clab-redes-$r vtysh -c "show daemons"
done
pausa

# ------------------------------------------------------------ 3. vizinhos

titulo "3. Vizinhos de cada roteador ($NOME)"
for r in r1 r2 r3 r4 r5; do
  echo
  mostra docker exec clab-redes-$r vtysh -c "$VIZ"
  docker exec clab-redes-$r vtysh -c "$VIZ" | sed -n "$RECORTE" | sed '/^$/d'
done
pausa

# ------------------------------------------------------------ 4. rotas

titulo "4. Rotas aprendidas pelo $NOME em R1"
cmd docker exec clab-redes-r1 vtysh -c "$ROTAS"
if [ "$PROTO" = bgp ]; then
  pausa
  titulo "4b. Tabela BGP de R1 (AS_PATH)"
  cmd docker exec clab-redes-r1 vtysh -c "show ip bgp"
fi
pausa

# ------------------------------------------------------------ 5. conectividade

titulo "5. Conectividade: ping de cada host para todos os outros"
IPS="192.168.1.10 192.168.2.10 192.168.3.10 192.168.4.10 192.168.5.10"
mostra 'docker exec clab-redes-h<origem> ping -c1 -W1 192.168.<destino>.10   # para cada par de hosts'
echo
printf "%-8s" "de/para"
for d in h1 h2 h3 h4 h5; do printf "%-6s" "$d"; done
echo
FALHOU=0
for o in 1 2 3 4 5; do
  printf "%-8s" "h$o"
  res=$(docker exec clab-redes-h$o sh -c "for ip in $IPS; do ping -c1 -W1 \$ip >/dev/null 2>&1 && printf 'ok ' || printf 'xx '; done")
  for v in $res; do
    if [ "$v" = ok ]; then printf "%s✔%s     " "$VERDE" "$FIM"; else printf "%s✘%s     " "$VERM" "$FIM"; FALHOU=1; fi
  done
  echo
done
[ "$FALHOU" = 0 ] && printf "%s✔ Todos os hosts se alcançam%s\n" "$VERDE" "$FIM"
pausa

# ------------------------------------------------------------ 6. caminho

titulo "6. Caminho de h1 até h4"
nota "Esperado com $NOME: $CAMINHO"
printf "%s\$ docker exec clab-redes-h1 traceroute -n 192.168.4.10%s\n" "$AMAR" "$FIM"
docker exec clab-redes-h1 traceroute -n -q 1 -w 1 192.168.4.10 | nomear
echo
cmd docker exec clab-redes-h1 ping -c 4 192.168.4.10
pausa

# ------------------------------------------------------------ 7. falha

if [ "$FALHA" = 1 ]; then
  set -- $CORTE
  A=$1; IA=$2; B=$3; IB=$4
  titulo "7. Falha: cortando o enlace ${A^^}–${B^^} (100% de perda)"
  case $PROTO in
    rip)  nota "O RIP só percebe quando a rota expira (timeout 180 s). Pode levar ~3 min." ;;
    ospf) nota "O OSPF percebe quando o vizinho fica 40 s sem Hello (dead interval)." ;;
    bgp)  nota "O BGP percebe quando a sessão fica 180 s sem keepalive (hold time)." ;;
  esac
  mostra 'docker exec clab-redes-h1 ping -c1 -W1 192.168.4.10   # a cada segundo'
  echo
  pingar() { docker exec clab-redes-h1 ping -c1 -W1 192.168.4.10 2>/dev/null | grep -oE 'time=[0-9.]+ ms' | cut -d= -f2; }
  for i in 1 2 3; do printf "  %s✔ resposta em %s%s\n" "$VERDE" "$(pingar)" "$FIM"; sleep 1; done
  echo
  nota "Perda de 100% nas duas pontas com tc netem (a interface continua up):"
  tc_em "$A" replace dev "$IA" root netem loss 100%
  tc_em "$B" replace dev "$IB" root netem loss 100%
  T0=$(date +%s)
  printf "%s>>> Enlace %s–%s cortado%s\n\n" "$VERM" "${A^^}" "${B^^}" "$FIM"
  while true; do
    ms=$(pingar)
    [ -n "$ms" ] && break
    [ $(( $(date +%s) - T0 )) -gt 300 ] && break
    printf "\r  %s✘ sem resposta há %3s s%s" "$VERM" "$(( $(date +%s) - T0 ))" "$FIM"
  done
  DT=$(( $(date +%s) - T0 ))
  printf "\n  %s✔ resposta em %s%s\n" "$VERDE" "$ms" "$FIM"
  for i in 1 2 3; do sleep 1; printf "  %s✔ resposta em %s%s\n" "$VERDE" "$(pingar)" "$FIM"; done
  if [ "$PROTO" = rip ]; then
    MSG="RTT menor: agora passa por R2 e R5, fora dos enlaces lentos"
  else
    MSG="RTT maior: agora passa pelos enlaces lentos, via R3"
  fi
  printf "\n%s>>> Conectividade de volta após ~%s s (%s)%s\n" "$VERDE" "$DT" "$MSG" "$FIM"
  pausa

  titulo "8. Novo caminho de h1 até h4"
  mostra docker exec clab-redes-h1 traceroute -n 192.168.4.10
  docker exec clab-redes-h1 traceroute -n -q 1 -w 1 192.168.4.10 | nomear
  pausa

  titulo "9. Restaurando o enlace ${A^^}–${B^^}"
  if [ "$LENTO" = 1 ]; then
    nota "Enlace lento: volta a ter só o atraso de 10 ms em cada ponta"
    tc_em "$A" replace dev "$IA" root netem delay 10ms
    tc_em "$B" replace dev "$IB" root netem delay 10ms
  else
    nota "Enlace normal: remove a regra de perda nas duas pontas"
    tc_em "$A" del dev "$IA" root
    tc_em "$B" del dev "$IB" root
  fi
  printf "%s✔ Enlace restaurado%s\n" "$VERDE" "$FIM"
fi

printf "\n%sFim da demonstração com %s.%s\n" "$VERDE" "$NOME" "$FIM"

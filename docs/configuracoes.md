# Configurações

Todas as configurações são geradas pelo `gerar_configs.py` a partir de `topologia.py`. Para mudar um endereço, timer ou custo, altere `topologia.py` e rode `python3 gerar_configs.py`. Não edite os arquivos de `configs/` à mão, porque eles são sobrescritos.

Os protocolos **não rodam ao mesmo tempo**. Cada cenário tem a própria pasta (`configs/rip`, `configs/ospf`, `configs/bgp`), e o arquivo `daemons` de cada uma liga só o daemon daquele protocolo.

## Como a configuração chega no roteador

| Arquivo no repositório | Montado no container como | Função |
|---|---|---|
| `configs/<proto>/rN.conf` | `/etc/frr/frr.conf` | Configuração do FRR (interfaces e protocolo) |
| `configs/<proto>/daemons` | `/etc/frr/daemons` | Quais daemons do FRR sobem |
| `configs/vtysh.conf` | `/etc/frr/vtysh.conf` | Faz o `vtysh` usar o `frr.conf` integrado |

O `experimento.py subir <proto>` copia os arquivos do cenário escolhido para `configs/ativo/`, e o `topologia.clab.yml` monta sempre `configs/ativo/`. Assim, a mesma topologia serve para os três cenários.

## Parte comum a todos os cenários

Em todos os roteadores:

```
frr defaults traditional
hostname r1
service integrated-vtysh-config
!
interface lo
 ip address 10.255.0.1/32
exit
!
interface eth1
 description enlace-r2
 ip address 10.0.12.1/30
exit
!
interface eth2
 description enlace-r3
 ip address 10.0.13.1/30
exit
!
interface eth3
 description lan-h1
 ip address 192.168.1.1/24
exit
```

| Linha | Significado |
|---|---|
| `frr defaults traditional` | Usa os padrões clássicos do FRR (timers e comportamentos tradicionais, não os do perfil "datacenter") |
| `service integrated-vtysh-config` | Toda a configuração fica em um só arquivo (`frr.conf`) |
| `interface ... ip address` | O endereçamento é feito pelo FRR (zebra), e não pelo Containerlab |

No `topologia.clab.yml`, cada roteador recebe as sysctls `net.ipv4.ip_forward=1`, para encaminhar pacotes, e `rp_filter=0`, para não descartar pacotes que chegam por um caminho assimétrico durante a reconvergência.

## RIP (v2)

```
router rip
 version 2
 timers basic 30 180 120
 network eth1
 network eth2
 network eth3
 network lo
 passive-interface eth3
 passive-interface lo
exit
```

| Linha | Significado |
|---|---|
| `version 2` | RIPv2: multicast 224.0.0.9, suporte a máscara (CIDR) |
| `timers basic 30 180 120` | Update a cada 30 s, rota expira em 180 s sem atualização, e é removida 120 s depois (garbage) |
| `network ethN` / `network lo` | Interfaces que participam do RIP; as redes delas são anunciadas |
| `passive-interface eth3` / `lo` | Anuncia a rede, mas não envia updates por ali (não há roteadores do outro lado) |

Métrica: número de saltos (máximo 15). Tráfego de controle: UDP 520.

## OSPF (área 0)

```
interface lo
 ip ospf area 0
 ip ospf passive
!
interface eth1
 ip ospf area 0
 ip ospf network point-to-point
 ip ospf hello-interval 10
 ip ospf dead-interval 40
 ip ospf cost 10
!
interface eth2
 ip ospf area 0
 ip ospf network point-to-point
 ip ospf hello-interval 10
 ip ospf dead-interval 40
 ip ospf cost 100
!
interface eth3
 ip ospf area 0
 ip ospf passive
!
router ospf
 ospf router-id 10.255.0.1
```

| Linha | Significado |
|---|---|
| `ip ospf area 0` | Toda a rede é um único domínio, na área de backbone |
| `ip ospf network point-to-point` | Enlaces /30 com dois roteadores: sem eleição de DR/BDR, a adjacência sobe mais rápido |
| `hello-interval 10` / `dead-interval 40` | Hello a cada 10 s; o vizinho é declarado morto após 40 s sem hello |
| `ip ospf cost 10` / `100` | Custo normal e custo dos enlaces lentos (R1–R3 e R3–R4) |
| `ip ospf passive` | Anuncia a rede, mas não forma adjacência (loopback e rede do host) |
| `ospf router-id` | Identificador do roteador, igual à loopback |

Métrica: soma dos custos. Tráfego de controle: protocolo IP 89.

## BGP (3 Sistemas Autônomos)

Exemplo de R1 (AS 65001), que tem um par iBGP (R2) e um par eBGP (R3):

```
router bgp 65001
 bgp router-id 10.255.0.1
 no bgp ebgp-requires-policy
 timers bgp 60 180
 neighbor 10.0.12.2 remote-as 65001
 neighbor 10.0.12.2 description iBGP-r2
 neighbor 10.0.12.2 timers connect 10
 neighbor 10.0.13.2 remote-as 65002
 neighbor 10.0.13.2 description eBGP-r3
 neighbor 10.0.13.2 timers connect 10
 !
 address-family ipv4 unicast
  network 10.255.0.1/32
  network 192.168.1.0/24
  neighbor 10.0.12.2 activate
  neighbor 10.0.12.2 next-hop-self
  neighbor 10.0.13.2 activate
 exit-address-family
```

| Linha | Significado |
|---|---|
| `router bgp 65001` | Número do AS do roteador |
| `no bgp ebgp-requires-policy` | O FRR exige uma política de filtro em sessões eBGP; aqui é desligado para anunciar tudo |
| `timers bgp 60 180` | Keepalive a cada 60 s; a sessão cai após 180 s sem keepalive (hold time) |
| `neighbor X remote-as Y` | Mesmo AS = iBGP, AS diferente = eBGP |
| `timers connect 10` | Tenta reabrir a sessão TCP a cada 10 s (o padrão é 120 s), para o lab subir rápido |
| `network ...` | Prefixos que o roteador origina: a própria loopback e a rede do host |
| `next-hop-self` | Em iBGP, troca o next-hop pelo próprio IP, para o par interno alcançar rotas vindas de fora do AS |

| Roteador | AS | Pares iBGP | Pares eBGP |
|---|---|---|---|
| r1 | 65001 | r2 | r3 (65002) |
| r2 | 65001 | r1 | r5 (65003) |
| r3 | 65002 | - | r1 (65001), r4 (65003) |
| r4 | 65003 | r5 | r3 (65002) |
| r5 | 65003 | r4 | r2 (65001) |

Como os pares iBGP estão diretamente conectados, não é preciso um IGP por baixo, e o BGP roda sozinho. Os enlaces entre roteadores não são anunciados; só loopbacks e redes de host. Por isso o BGP tem menos rotas por roteador (8 aprendidas, contra 11 no RIP e no OSPF).

Critério de escolha: menor AS_PATH. Tráfego de controle: TCP 179.

## Arquivo `daemons`

Igual nos três cenários, só muda qual daemon está em `yes`:

| Cenário | Daemon ligado |
|---|---|
| RIP | `ripd=yes` |
| OSPF | `ospfd=yes` |
| BGP | `bgpd=yes` |

O `zebra` (tabela de rotas do kernel), o `mgmtd` e o `staticd` sempre sobem. As opções `-A 127.0.0.1` fazem os daemons escutarem o vty só localmente, e `-s 90000000` aumenta o buffer netlink do zebra.

## Hosts

Os hosts não usam FRR. O `topologia.clab.yml` configura cada um via `exec` ao subir:

```
ip addr add 192.168.1.10/24 dev eth1
ip route add 192.168.0.0/16 via 192.168.1.1
ip route add 10.0.0.0/8 via 192.168.1.1
```

A rota padrão continua na `eth0` (gerência), por isso as rotas para o lab são específicas.

## Configurações aplicadas em tempo de execução

Não ficam em arquivo, e são aplicadas pelo `experimento.py` com a imagem netshoot dentro do namespace de rede do roteador:

| Ação | Comando | Quando |
|---|---|---|
| Atraso nos enlaces lentos | `tc qdisc replace dev ethN root netem delay 10ms` em r1, r3 e r4 | Logo após subir o cenário |
| Corte de enlace | `tc qdisc replace dev ethN root netem loss 100%` nas duas pontas | Teste de falha |
| Restauração | Volta o `netem delay` (enlace lento) ou remove o qdisc | Após o teste de falha |

O corte usa perda de 100%, e não `ip link set down`, para a interface continuar *up*: assim cada protocolo precisa detectar a falha pelos próprios timers, que é o que se quer comparar. Se o kernel não tiver `netem`, o script usa `iptables -j DROP`.

## Timers comparados

| Protocolo | Timer | Valor | Efeito na reconvergência |
|---|---|---|---|
| RIP | update / timeout / garbage | 30 / 180 / 120 s | A rota só expira após 180 s sem update → ~180 a 210 s |
| OSPF | hello / dead | 10 / 40 s | Vizinho morto após 40 s sem hello → ~30 a 40 s |
| BGP | keepalive / hold | 60 / 180 s | Sessão cai após 180 s sem keepalive → ~120 a 180 s |

## Complexidade de configuração

O `gerar_configs.py` conta as linhas específicas de cada protocolo nos 5 roteadores e grava em `configs/complexidade.json`:

| Protocolo | Linhas |
|---|---|
| RIP | 40 |
| OSPF | 60 |
| BGP | 54 |

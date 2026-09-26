# Guia de uso

Como preparar o ambiente, subir a rede, rodar os experimentos e o que é cada arquivo do repositório.

## 1. Preparar o ambiente

Siga a seção [Preparação do ambiente](../README.md#preparação-do-ambiente) do README para o seu sistema (Linux, macOS ou Windows). No fim, você precisa ter, dentro do Linux:

| Ferramenta | Verificar com |
|---|---|
| Docker | `docker --version` |
| Containerlab | `containerlab version` |
| Python 3 e matplotlib | `python3 -c "import matplotlib"` |
| Imagem FRR | `docker image ls quay.io/frrouting/frr` |
| Imagem netshoot | `docker image ls nicolaka/netshoot` |

No macOS, todos os comandos abaixo rodam dentro da VM (`orb -m redes`), na pasta do repositório em `/Users/...`.

## 2. (Opcional) Alterar a topologia

Só é necessário se você mudar algo em `topologia.py` (endereços, custos, timers, atraso):

```bash
python3 gerar_configs.py
```

Isso regera `configs/` e `topologia.clab.yml`.

## 3. Subir um cenário e explorar

```bash
sudo python3 experimento.py subir ospf     # ou rip, bgp
```

Sobe os 10 containers, aplica o atraso nos enlaces lentos e espera todos os hosts se alcançarem.

### Ver a rede rapidamente

| O que ver | Comando |
|---|---|
| Lista de nós, estado e IP de gerência | `sudo containerlab inspect -t topologia.clab.yml` |
| Desenho da topologia no navegador | `sudo containerlab graph -t topologia.clab.yml` e abrir `http://<ip-da-vm>:50080` (no OrbStack: `http://localhost:50080`) |
| Containers rodando | `docker ps --format "table {{.Names}}\t{{.Status}}"` |
| Interfaces de um nó | `docker exec clab-redes-r1 ip -br addr` |
| Console do roteador | `docker exec -it clab-redes-r1 vtysh` |
| Shell de um host | `docker exec -it clab-redes-h1 bash` |

### Comandos do roteador (dentro do `vtysh`)

| Protocolo | Comandos úteis |
|---|---|
| Todos | `show ip route`, `show interface brief`, `show running-config` |
| RIP | `show ip rip`, `show ip rip status` |
| OSPF | `show ip ospf neighbor`, `show ip ospf database`, `show ip ospf interface` |
| BGP | `show bgp summary`, `show ip bgp`, `show ip bgp neighbors` |

Também dá para rodar de fora: `docker exec clab-redes-r1 vtysh -c "show ip route"`.

No macOS 15 ou mais novo, o navegador precisa da permissão de Rede Local (Ajustes do Sistema → Privacidade e Segurança → Rede Local) para abrir `redes.orb.local`. Sem ela, dá `ERR_ADDRESS_UNREACHABLE`. Pelo `localhost` não precisa.

### Testes a partir dos hosts

```bash
docker exec clab-redes-h1 ping -c 4 192.168.4.10
docker exec clab-redes-h1 traceroute -n 192.168.4.10
```

### Ver o tráfego de controle ao vivo

```bash
docker run --rm -it --net container:clab-redes-r1 nicolaka/netshoot \
  tcpdump -i any -n "udp port 520 or ip proto 89 or tcp port 179"
```

### Simular uma falha

```bash
sudo python3 experimento.py falha r1 r2       # perda de 100% nas duas pontas do enlace
sudo python3 experimento.py restaurar r1 r2
```

Com um `ping` contínuo em outro terminal, dá para ver o tempo sem resposta até o protocolo reconvergir.

### Derrubar

```bash
sudo python3 experimento.py derrubar
```

## 4. Rodar o experimento completo

```bash
sudo python3 experimento.py rodar todos          # RIP, OSPF e BGP em sequência (~16 a 20 min)
sudo python3 experimento.py rodar ospf           # um cenário só
sudo python3 experimento.py rodar bgp --manter   # não derruba o lab no final
sudo python3 experimento.py rodar rip --regime 60  # janela de regime de 60 s (padrão 120)
```

Etapas de cada cenário:

1. Sobe o lab com as configs do protocolo e aplica o atraso nos enlaces lentos.
2. Inicia `tcpdump` em cada roteador, capturando só o tráfego de controle enviado.
3. Mede a convergência inicial (até todos os hosts se alcançarem).
4. Espera 20 s e abre a janela de regime (120 s): rotas, traceroute h1 → h4, RTT entre hosts, CPU e memória.
5. Teste de falha: ping h1 → h4 a cada 100 ms e corte do primeiro enlace do caminho ativo que sai de R1. Mede o tempo até o ping voltar.
6. Registra rotas e caminho depois da falha, restaura o enlace e derruba o lab.

## 5. Gerar as métricas

```bash
python3 analisar.py
```

Não precisa de `sudo`. Lê `resultados/<proto>/` e gera o resumo, os CSVs e os gráficos.

## Arquivos do repositório

### Código e configuração

| Arquivo | O que é |
|---|---|
| `topologia.py` | Definição única da topologia: roteadores, AS, enlaces, endereços, custos, timers, atraso e filtro de captura |
| `gerar_configs.py` | Gera `configs/` e `topologia.clab.yml` a partir de `topologia.py`, e conta as linhas de configuração |
| `topologia.clab.yml` | Topologia do Containerlab: nós, imagens, arquivos montados e enlaces (gerado) |
| `experimento.py` | Orquestra tudo: sobe e derruba cenários, aplica atraso, corta enlaces, captura tráfego e mede |
| `analisar.py` | Calcula as métricas e gera resumo, CSVs e gráficos |
| `configs/rip/`, `configs/ospf/`, `configs/bgp/` | `rN.conf` de cada roteador e o `daemons` do cenário (gerados) |
| `configs/vtysh.conf` | Configuração do `vtysh`, igual em todos os roteadores |
| `configs/complexidade.json` | Linhas de configuração de cada protocolo nos 5 roteadores |
| `configs/ativo/` | Cópia do cenário em uso, montada nos containers. Criada ao subir, fora do git |
| `docs/` | Esta documentação |

Detalhes de cada configuração em [configuracoes.md](configuracoes.md), e da topologia em [topologia.md](topologia.md).

### Resultados brutos (`resultados/<proto>/`)

| Arquivo | Conteúdo |
|---|---|
| `r1.pcap` a `r5.pcap` | Tráfego de controle enviado por cada roteador durante todo o cenário (abre no Wireshark) |
| `eventos.json` | Instantes de cada etapa: deploy, convergência, janela de regime, falha, recuperação e fim |
| `rotas_antes.json` / `rotas_depois.json` | Rotas totais e aprendidas pelo protocolo em cada roteador, antes e depois da falha |
| `estado_antes.txt` / `estado_depois.txt` | Saída dos comandos `show` do protocolo em cada roteador |
| `traceroute_antes.json` / `traceroute_depois.json` | Caminho h1 → h4 antes e depois da falha |
| `rtt.json` | RTT mínimo, médio, máximo, desvio e perda entre pares de hosts |
| `recursos.json` | Amostras de CPU e memória de cada roteador durante a janela de regime |
| `ping_falha.txt` | Saída do ping h1 → h4 (a cada 100 ms, com timestamp) durante o teste de falha |

### Métricas processadas (`resultados/`)

| Arquivo | Conteúdo |
|---|---|
| `resumo.csv` / `resumo.md` | Uma linha (ou coluna) por protocolo com todas as métricas consolidadas |
| `csv/eventos.csv` | Eventos de cada cenário, com timestamp e tempo relativo à falha |
| `csv/pacotes_controle.csv` | Cada pacote de controle: protocolo, roteador, tempo e tamanho |
| `csv/controle_por_segundo.csv` | Pacotes e bytes de controle por segundo, relativos à falha |
| `csv/ping_falha.csv` | Cada resposta do ping durante a falha: tempo, sequência e RTT |
| `csv/rotas.csv` | Rotas totais e aprendidas por roteador, antes e depois da falha |
| `csv/rtt.csv` | RTT entre pares de hosts (mín., média, máx., desvio, perda) |
| `csv/recursos.csv` | Amostras de CPU e memória por roteador |
| `csv/caminho_h1_h4.csv` | Saltos do traceroute h1 → h4 antes e depois da falha |
| `graficos/*.png` | Gráficos comparativos gerados a partir das métricas |

### Métricas do `resumo.csv`

| Coluna | Significado |
|---|---|
| `conv_inicial_s` | Tempo do deploy até todos os hosts se alcançarem |
| `pacotes_regime_por_min`, `taxa_regime_bps`, `bytes_regime_por_min` | Tráfego de controle da rede inteira na janela de regime |
| `reconvergencia_s` | Tempo da falha até a primeira resposta do ping |
| `interrupcao_s` | Maior intervalo sem resposta do ping |
| `pings_perdidos` | Pings perdidos nesse intervalo |
| `pacotes_durante_falha`, `bytes_durante_falha` | Tráfego de controle entre a falha e a reconvergência |
| `pacotes_total`, `bytes_total` | Tráfego de controle do cenário inteiro |
| `rotas_por_roteador`, `rotas_aprendidas_por_roteador` | Tamanho médio da tabela de rotas |
| `rotas_aprendidas_total`, `rotas_aprendidas_total_pos_falha` | Soma nos 5 roteadores, antes e depois da falha |
| `rtt_<par>_ms` | RTT médio entre hosts antes da falha |
| `cpu_pct_por_roteador`, `mem_mib_por_roteador` | Consumo médio dos roteadores |
| `linhas_config` | Linhas de configuração do protocolo |
| `caminho_h1_h4_antes`, `caminho_h1_h4_depois`, `enlace_cortado` | Caminho e enlace do teste de falha |

## Solução de problemas

Veja a seção [Solução de problemas](../README.md#solução-de-problemas) do README.

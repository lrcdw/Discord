# TOTAL_CORNERS v3.1.3 — preparação pré-observação

Preparado em: 2026-09-01 14:00:50 UTC  
Estado: `PREPARED_NOT_ACTIVATED_NO_COHORT`  
Modo: `SHADOW`

## Avanço realizado

A causa dos 219 conflitos de integridade da auditoria v3.1.2 foi isolada. Os 21 raws de odds foram reexaminados sem novas consultas. Apenas a FanDuel apresentou chaves de resultado duplicadas com preços incompatíveis.

Nos 15 raws que continham FanDuel, havia 256 combinações repetidas de linha/lado com preços diferentes no mesmo `last_update`. Duas dessas snapshots também estavam vencidas pelo limite de 300 segundos; a auditoria v3.1.2, que verificava frescor antes do conteúdo, expôs 219 conflitos elegíveis por frescor. Nenhum campo do provedor informa qual preço seria autoritativo.

## Política v3.1.3

A v3.1.3 não escolhe um preço, não calcula média e não usa a ordem dos outcomes. Quando uma casa contém preços conflitantes para a mesma linha, lado e timestamp, toda a snapshot dessa casa para a célula é colocada em quarentena. As demais casas do mesmo raw continuam independentes e só são válidas quando oferecem Over e Under da mesma linha, casa e timestamp.

Duplicatas exatamente iguais podem ser colapsadas porque não adicionam informação. Timestamp ausente, futuro ou com mais de 300 segundos também coloca somente a snapshot da casa em quarentena. Hash inválido, coleta fora da janela, identidade inconsistente ou vazamento de credencial continuam falhas sistêmicas.

Essa mudança altera apenas o alcance da falha. Permanecem congelados:

- `TOTAL_CORNERS`, pré-jogo, partida completa e linhas de meio ponto;
- T-60, T-30 e T-15;
- idade máxima de 300 segundos;
- cobertura mínima de 90%;
- piso parcial de 70%;
- admissão da casa em 80% nos três horizontes;
- mínimo de 25 jogos por competição;
- reserva obrigatória de 100 créditos;
- proibição de acesso pago, histórico, vazamento e promoção automática.

FanDuel continua entre as dez casas candidatas. Ela não foi removida após observação; a regra de quarentena é genérica e se aplica igualmente a qualquer casa.

## Replay diagnóstico da coorte encerrada

A v3.1.3 foi aplicada aos raws antigos apenas como `ENGINEERING_REPLAY_ONLY`. O replay produziu:

- integridade sistêmica: `PASS`;
- qualidade local das casas: `FAIL_QUARANTINED`;
- 223 diagnósticos locais: 219 conflitos e 4 timestamps vencidos;
- 17 snapshots de casa em quarentena;
- 21 células cobertas e 99 não cobertas, sem mudança do denominador;
- `step_h_status=ENGINEERING_REPLAY_ONLY`;
- `step_i_allowed=false`.

Esse replay valida o código, mas não prova cobertura e não reclassifica a v3.1.2. Os três relatórios finais da coorte anterior permanecem byte a byte idênticos aos hashes congelados.

## Infraestrutura preparada, mas inativa

O workflow antigo v3.1.2 foi desativado localmente para impedir sua reativação acidental. Foi criado um workflow v3.1.3 somente manual, sem agenda automática. Ele exige um registro de coorte que confirme separadamente autorização de ativação, publicação no repositório público e janela de uso da chave. Sem essas três confirmações, falha antes da coleta.

O coletor v3.1.3 preserva uma tentativa por célula, não repete tentativas persistidas e aceita no máximo 19.800 segundos por janela de execução. Nenhuma chamada de rede ou consumo de crédito foi realizado nesta preparação.

## Estado de promoção

Não existe coorte v3.1.3 ativa. O Passo I continua bloqueado. A próxima ação com efeito externo exige autorização explícita para publicar no repositório público e uma nova janela de uso da chave; só então o denominador poderá ser congelado antes da primeira observação.

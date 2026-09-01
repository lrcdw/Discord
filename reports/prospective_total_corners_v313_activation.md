# TOTAL_CORNERS v3.1.3A — autorização e ativação

## Estado

A v3.1.3 foi autorizada em 2026-09-01 para publicação no repositório público `lrcdw/Discord` e para uso da chave gratuita em consultas atuais da nova coorte até 2026-09-07 inclusive. A reserva mínima foi elevada de 100 para 150 créditos antes da inscrição da coorte e antes de qualquer observação de odds.

## Controles preservados

- modo `SHADOW` e `TOTAL_CORNERS` de jogo completo, pré-jogo e linhas de meio ponto;
- horizontes T-60, T-30 e T-15, idade máxima de 300 segundos e ausência de vazamento;
- uma tentativa por célula, falha fechada, raws imutáveis e hashes obrigatórios;
- thresholds, denominador e aliases inalterados;
- módulos HOME/AWAY TEAM TOTAL CORNERS permanecem adiados e não bloqueiam esta coorte;
- nenhum pagamento, upgrade, endpoint histórico ou persistência da chave é permitido.

## Execução

O workflow de ativação verifica a cota gratuitamente, baixa uma única vez o denominador independente, congela a coorte e publica o registro ativo. A ativação é recusada se o custo máximo projetado não preservar 150 créditos. O workflow de coleta roda em infraestrutura sempre ligada, consulta somente células pré-registradas dentro da janela e expira automaticamente após a janela autorizada.

## Separação de evidência

Os testes e o replay anterior validam a engenharia. A prova de cobertura de mercado só pode vir dos snapshots prospectivos da nova coorte. O Passo I permanece bloqueado até a conclusão dos gates formais do Passo H.

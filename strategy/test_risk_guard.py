"""Pruebas del guardián de riesgo (reglas de Cerebro/02-Areas/Reglas de riesgo.md). Uso: python strategy/test_risk_guard.py"""
import risk_guard as G

EQ = 1120.0   # 1000 € en $

def t(name, got, want):
    assert got == want, f"{name}: {got!r} != {want!r}"
    print("ok ·", name)

# riesgo 2 %: 22,4 $ -> con stop de 10 $ caben 2 oz
t("tamaño 2 %", G.size_entry(EQ, EQ, 0, 4000, 10.0)[0], 2.0)
# 1 oz ya arriesga 3 % -> no se opera (nunca se fuerza el tamaño)
q, why = G.size_entry(EQ, EQ, 0, 4000, 33.6); t("1 oz > 2 % se rechaza", q, 0.0); assert "1 oz" in why
# sin stop
t("sin stop", G.size_entry(EQ, EQ, 0, 4000, 0.0)[0], 0.0)
# límite diario: hoy -4 % y la operación arriesgaría 2 % -> no cabe
t("no cabe en el -5 % diario", G.size_entry(EQ * 0.96, EQ, 2, 4000, 10.0)[0], 0.0)
# hoy -1 % y riesgo 2 % -> cabe
t("cabe en el -5 % diario", G.size_entry(EQ * 0.99, EQ, 1, 4000, 10.0)[0], 2.0)
# 4 pérdidas en el día paran el día
t("4 pérdidas paran el día", G.size_entry(EQ * 1.05, EQ, 4, 4000, 10.0)[0], 0.0)
# ratio < 2:1
q, why = G.size_entry(EQ, EQ, 0, 4000, 10.0, targets_r=[1.0, 2.0]); t("ratio 1,5:1 se rechaza", q, 0.0)
t("ratio 3:1 se acepta", G.size_entry(EQ, EQ, 0, 4000, 10.0, targets_r=[2, 3, 4])[0], 2.0)
# el stop solo puede acercarse
t("largo: subir stop", G.check_stop_change(1, 3990, 3995, 4000)[0], True)
t("largo: bajar stop", G.check_stop_change(1, 3990, 3985, 4000)[0], False)
t("largo: stop por encima del precio", G.check_stop_change(1, 3990, 4001, 4000)[0], False)
t("corto: bajar stop", G.check_stop_change(-1, 4010, 4005, 4000)[0], True)
t("corto: subir stop", G.check_stop_change(-1, 4010, 4015, 4000)[0], False)
print("Todas las reglas se cumplen.")

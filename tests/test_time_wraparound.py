from devac.matcher import EventMatcher
from devac.interval_tree import IntervalTree
from devac.audit import EventRelation, _as_set_spec, _covers, _intersects


def test_matcher_rango_cruza_medianoche_coincidencias():
    rng = "23:00-06:00"
    assert EventMatcher.is_time_in_range(rng, "23:30") is True
    assert EventMatcher.is_time_in_range(rng, "00:00") is True
    assert EventMatcher.is_time_in_range(rng, "05:59") is True
    assert EventMatcher.is_time_in_range(rng, "06:00") is True  # extremo final inclusivo


def test_matcher_rango_cruza_medianoche_no_coincidencias():
    rng = "23:00-06:00"
    assert EventMatcher.is_time_in_range(rng, "12:00") is False
    assert EventMatcher.is_time_in_range(rng, "06:01") is False


def test_matcher_extremo_inicial_inclusivo():
    assert EventMatcher.is_time_in_range("23:00-06:00", "23:00") is True


def test_matcher_rango_normal_sigue_funcionando():
    rng = "08:00-18:00"
    assert EventMatcher.is_time_in_range(rng, "08:00") is True
    assert EventMatcher.is_time_in_range(rng, "18:00") is True
    assert EventMatcher.is_time_in_range(rng, "12:00") is True
    assert EventMatcher.is_time_in_range(rng, "07:59") is False
    assert EventMatcher.is_time_in_range(rng, "18:01") is False


def test_interval_tree_encuentra_rango_cruza_medianoche_una_sola_vez():
    tree = IntervalTree()
    tree.insert("23:00", "06:00", community_id="c1", policy_id="p1")

    comunidades = tree.query("02:00")
    assert comunidades == {"c1"}

    pares = tree.query_with_policies("02:00")
    assert pares == [("c1", "p1")]  # una sola vez aunque se guarde en dos pedazos


def test_interval_tree_rango_cruza_medianoche_fuera_de_rango():
    tree = IntervalTree()
    tree.insert("23:00", "06:00", community_id="c1", policy_id="p1")
    assert tree.query("12:00") == set()


def test_interval_tree_remove_policy_quita_ambos_segmentos():
    tree = IntervalTree()
    tree.insert("23:00", "06:00", community_id="c1", policy_id="p1")
    assert len(tree) == 2  # 23:00-23:59 y 00:00-06:00
    tree.remove_policy("p1")
    assert tree.query("02:00") == set()
    assert tree.query("23:30") == set()


def test_audit_cubre_rango_mas_amplio():
    amplio = _as_set_spec({"range": ["22:00", "07:00"]})
    estrecho = _as_set_spec({"range": ["23:00", "06:00"]})
    assert _covers(amplio, estrecho) is True


def test_audit_no_cubre_cuando_se_sale_del_rango():
    a = _as_set_spec({"range": ["23:00", "06:00"]})
    b = _as_set_spec({"range": ["05:00", "08:00"]})
    assert _covers(a, b) is False


def test_audit_traslape_con_rango_normal_que_cruza_hacia_la_manana():
    a = _as_set_spec({"range": ["23:00", "06:00"]})
    b = _as_set_spec({"range": ["05:00", "08:00"]})
    assert _intersects(a, b) is True


def test_audit_sin_traslape_cuando_estan_en_horas_distintas():
    a = _as_set_spec({"range": ["23:00", "06:00"]})
    b = _as_set_spec({"range": ["07:00", "22:00"]})
    assert _intersects(a, b) is False

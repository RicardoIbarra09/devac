import re
from typing import Any, Dict, List, Tuple

from devac.models import Policy


# error con numero de linea
class DPLSyntaxError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(f"Línea {line}: {message}")
        self.line = line


# dimension del DPL -> campo de Event
_DIM_TO_FIELD = {
    "SUBJECT": "subject",
    "RESOURCE": "asset",
    "LOCATION": "space",
    "TIME": "time",
    "ACTION": "action",
}
_EFFECT_MAP = {"ALLOW": "allow", "DENY": "deny"}
_ONDENY_MAP = {"ALERT": "alert", "ESCALATE": "escalate"}

# tokens del lenguaje
_TOKEN_RE = re.compile(r"""
    (?P<NEWLINE>\n)
  | (?P<WS>[ \t\r]+)
  | (?P<LBRACE>\{) | (?P<RBRACE>\})
  | (?P<LPAREN>\() | (?P<RPAREN>\))
  | (?P<COMMA>,)   | (?P<SEMI>;)
  | (?P<EQ>=)      | (?P<STAR>\*)
  | (?P<WORD>[^\s,;(){}=*]+)
""", re.VERBOSE)


def _tokenize(text: str) -> List[Tuple[str, str, int]]:
    text = re.sub(r"//[^\n]*", "", text)  # quita comentarios //
    tokens: List[Tuple[str, str, int]] = []
    line = 1
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        value = m.group()
        if kind == "NEWLINE":
            line += 1
            continue
        if kind == "WS":
            continue
        tokens.append((kind, value, line))
    tokens.append(("EOF", "", line))
    return tokens


# parser recursivo descendente
class _Parser:
    def __init__(self, tokens: List[Tuple[str, str, int]]):
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Tuple[str, str, int]:
        return self._tokens[self._pos]

    def _advance(self) -> Tuple[str, str, int]:
        tok = self._tokens[self._pos]
        if tok[0] != "EOF":
            self._pos += 1
        return tok

    def _expect_kind(self, kind: str) -> Tuple[str, str, int]:
        tok = self._peek()
        if tok[0] != kind:
            raise DPLSyntaxError(f"se esperaba {kind}, se encontró '{tok[1] or '<fin de archivo>'}'", tok[2])
        return self._advance()

    def _expect_word(self, word: str) -> Tuple[str, str, int]:
        tok = self._peek()
        if tok[0] != "WORD" or tok[1] != word:
            raise DPLSyntaxError(f"se esperaba la palabra clave '{word}', se encontró '{tok[1] or '<fin de archivo>'}'", tok[2])
        return self._advance()

    def parse_document(self) -> List[Dict[str, Any]]:
        policies = []
        while self._peek()[0] != "EOF":
            policies.append(self._parse_policy())
        return policies

    # POLICY id EFFECT ALLOW|DENY [RECONFIGURE_ON (...)] [ON_DENY ...] { eventos }
    def _parse_policy(self) -> Dict[str, Any]:
        self._expect_word("POLICY")
        pid = self._expect_kind("WORD")[1]
        self._expect_word("EFFECT")
        effect_tok = self._expect_kind("WORD")[1]
        if effect_tok not in _EFFECT_MAP:
            raise DPLSyntaxError(f"efecto inválido '{effect_tok}', se esperaba ALLOW o DENY", self._peek()[2])
        effect = _EFFECT_MAP[effect_tok]

        reconfigure_on: List[str] = []
        on_deny = None

        # clausulas opcionales, en cualquier orden
        while self._peek()[0] == "WORD" and self._peek()[1] in ("RECONFIGURE_ON", "ON_DENY"):
            keyword = self._advance()[1]
            if keyword == "RECONFIGURE_ON":
                self._expect_kind("LPAREN")
                reconfigure_on.append(self._expect_kind("WORD")[1])
                while self._peek()[0] == "COMMA":
                    self._advance()
                    reconfigure_on.append(self._expect_kind("WORD")[1])
                self._expect_kind("RPAREN")
            else:
                ondeny_tok = self._expect_kind("WORD")[1]
                if ondeny_tok not in _ONDENY_MAP:
                    raise DPLSyntaxError(f"on_deny inválido '{ondeny_tok}', se esperaba ALERT o ESCALATE", self._peek()[2])
                on_deny = _ONDENY_MAP[ondeny_tok]

        self._expect_kind("LBRACE")
        events = []
        while self._peek()[0] != "RBRACE":
            events.append(self._parse_event())
        self._expect_kind("RBRACE")

        if not events:
            raise DPLSyntaxError(f"la política '{pid}' no tiene ningún EVENT", self._peek()[2])

        policy: Dict[str, Any] = {
            "policy_id": pid,
            "description": f"Política {pid} ({effect}) compilada desde DPL",
            "effect": effect,
            "events": events,
        }
        if reconfigure_on:
            policy["reconfigure_on"] = reconfigure_on
        if on_deny:
            policy["on_deny"] = on_deny
        return policy

    # EVENT id WHERE (dim op valor, ...);
    def _parse_event(self) -> Dict[str, Any]:
        self._expect_word("EVENT")
        eid = self._expect_kind("WORD")[1]
        self._expect_word("WHERE")
        self._expect_kind("LPAREN")

        components: Dict[str, Dict[str, Any]] = {}
        components.update(self._parse_attr())
        while self._peek()[0] == "COMMA":
            self._advance()
            components.update(self._parse_attr())

        self._expect_kind("RPAREN")
        self._expect_kind("SEMI")

        # tienen que venir las 5 dimensiones
        missing = set(_DIM_TO_FIELD.values()) - set(components.keys())
        if missing:
            raise DPLSyntaxError(f"al evento '{eid}' le faltan dimensiones: {sorted(missing)}", self._peek()[2])

        event: Dict[str, Any] = {"event_id": eid}
        event.update(components)
        return event

    # operadores: =, IN, RANGE
    def _parse_attr(self) -> Dict[str, Dict[str, Any]]:
        dim_tok = self._expect_kind("WORD")
        dim = dim_tok[1]
        if dim not in _DIM_TO_FIELD:
            raise DPLSyntaxError(f"dimensión desconocida '{dim}'", dim_tok[2])
        field = _DIM_TO_FIELD[dim]

        op_tok = self._peek()
        if op_tok[0] == "EQ":
            self._advance()
            value = self._parse_exact_or_wildcard()
        elif op_tok[0] == "WORD" and op_tok[1] == "IN":
            self._advance()
            value = self._parse_set()
        elif op_tok[0] == "WORD" and op_tok[1] == "RANGE":
            self._advance()
            value = self._parse_range()
        else:
            raise DPLSyntaxError(f"operador inválido '{op_tok[1]}', se esperaba =, IN o RANGE", op_tok[2])

        return {field: {"attribute": field, "value": value}}

    def _parse_exact_or_wildcard(self):
        tok = self._peek()
        if tok[0] == "STAR":
            self._advance()
            return "*"
        return self._expect_kind("WORD")[1]

    def _parse_set(self):
        self._expect_kind("LPAREN")
        values = [self._expect_kind("WORD")[1]]
        while self._peek()[0] == "COMMA":
            self._advance()
            values.append(self._expect_kind("WORD")[1])
        self._expect_kind("RPAREN")
        if len(values) < 2:
            raise DPLSyntaxError("IN requiere al menos 2 valores separados por coma", self._peek()[2])
        return {"in": values}

    def _parse_range(self):
        self._expect_kind("LPAREN")
        lo = self._expect_kind("WORD")[1]
        self._expect_kind("COMMA")
        hi = self._expect_kind("WORD")[1]
        self._expect_kind("RPAREN")
        return {"range": [lo, hi]}


# texto DPL -> lista de dicts con el formato de Policy
def compile_dpl(text: str) -> List[Dict[str, Any]]:
    tokens = _tokenize(text)
    return _Parser(tokens).parse_document()


def compile_dpl_to_policies(text: str) -> List[Policy]:
    return [Policy.model_validate(p) for p in compile_dpl(text)]


class DPLLoader:
    @staticmethod
    def from_file(filepath: str) -> List[Policy]:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
        return compile_dpl_to_policies(text)

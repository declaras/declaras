"""Quien esta autenticado y como se comprueba.

`principal` define la identidad, `jwks` guarda las llaves publicas con que se verifica la firma, y
`token` hace la verificacion. El portero que los usa vive en `api/deps.py`, que es donde FastAPI
espera encontrar las dependencias.
"""

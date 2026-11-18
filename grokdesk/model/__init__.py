"""Couche quantitative : volatilité, probabilité équitable, indicateurs,
calibration.

Aucun appel LLM ici, et c'est un choix argumenté : la probabilité que le spot
soit au-dessus du strike dans T secondes est le prix d'une option digitale,
pas une opinion. Le simulateur pump.fun d'origine le démontre à ses dépens —
son score « narrative » produit par le LLM était un `sha256 % 1000`, donc du
bruit, et son seul edge réel venait d'une règle déterministe.
"""

"""Les agents du desk, dans l'ordre du pipeline.

SPOTTER → PRIOR → EDGE → KELLY → TAKER → CLOSER, avec AUDIT en boucle de
retour. La hiérarchie héritée de la version pump.fun est conservée sur un
point essentiel : le risk engine garde la primauté et ne peut être contourné
par aucun agent, pas même la méta-couche.
"""

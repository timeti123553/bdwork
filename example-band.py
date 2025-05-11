from bdwork import standard


band_folder = '../band'

# ==================================================
# -------------- Plain Band Structure --------------
# ==================================================

standard.band_plain(
    folder=band_folder
)


# ==================================================
# --------------- SPD Band Structure ---------------
# ==================================================

standard.band_spd(
    folder=band_folder
)


# ==================================================
# ------------ Orbital Band Structure --------------
# ==================================================

standard.band_orbitals(
    folder=band_folder,
    orbitals=[0, 1, 2, 3, 4, 5, 6, 7, 8],
)


# ==================================================
# -------------- Atom Band Structure ---------------
# ==================================================

standard.band_atoms(
    folder=band_folder,
    atoms=[0, 1],
)


# ==================================================
# ---------- Atom Orbital Band Structure -----------
# ==================================================

standard.band_atom_orbitals(
    folder=band_folder,
    atom_orbital_dict={0:[1,3], 1:[1,7]},
)


# ==================================================
# ------------ Element Band Structure --------------
# ==================================================

standard.band_elements(
    folder=band_folder,
    elements=['In', 'As'],
)


# ==================================================
# ---------- Element SPD Band Structure ------------
# ==================================================

standard.band_element_spd(
    folder=band_folder,
    element_spd_dict={'As':'spd'},
)


# ==================================================
# ------- Element Orbitals Band Structure ----------
# ==================================================

standard.band_element_orbitals(
    folder=band_folder,
    element_orbital_dict={'As':[2], 'In':[3]},
)
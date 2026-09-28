"""MiD 2023 Wege columns no first-party code reads, dropped before the synthetic trip build.

``braunschweig.popsim.trips_stage`` joins every donor Wege row onto every synthetic person that
sources its plan from that donor, so each Wege column is copied once per synthetic TRIP, and
every intermediate copy the trip build makes of that table (the trip-time repair, the plan
repair, the sorts) carries all of them. The columns in :data:`UNUSED_MID_WEGE_COLUMNS` are read
nowhere in this repository -- not by the trip build, not by any stage downstream of it
(``synthesis.output`` writes an explicit column list), not by a configuration file -- so they
only cost memory, and dropping them before the join cannot change any value of the trip table
or of anything computed from it (ADR-0138).

The list is the complement of the columns first-party code reads, taken over the 2026-09 MiD 2023
B1 Wege delivery and kept in that delivery's column order; what each variable means is
documented in the MiD 2023 codeplan. The list claims nothing about the variables' content, only
that no first-party code reads them, and ``tests/test_unused_mid_wege_columns.py`` enforces
exactly that claim by scanning every first-party code and configuration file. **Rule for
maintainers:** when code starts reading one of these columns, remove it from the list in the
same change; the scan fails until that is done. A column a future delivery adds is never listed
here and is therefore kept -- the list only removes columns known to be unread.

Traceability does not depend on the dropped copies: every trip keeps its donor keys (``H_ID``,
``P_ID``, ``W_ID`` and the ``trip_key`` built from them), through which any MiD variable can be
joined back from the delivery for an ad-hoc analysis.

Pure pandas; no synpp.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_BYTES_PER_MEBIBYTE = 1024.0 * 1024.0

#: MiD 2023 Wege columns that no first-party code or configuration reads (2026-09 B1 delivery,
#: in the delivery's column order). See the module docstring for the claim and its enforcement.
UNUSED_MID_WEGE_COLUMNS = (
    "W_HOCH", "W_GEW_PKM", "W_HOCH_PKM", "MODE_HH", "BASISAUF", "PROXY_01", "PAPVERS", "INT_TYP",
    "M_ETAP", "W_ET_IN", "ST_MONAT", "ST_JAHR", "ST_WOCHE", "saison", "quartal", "P_STWETTER",
    "W_SO2", "sz_gr1", "sz_gr2", "az_gr1", "az_gr2", "W_FOLGETAG", "wegmin_gr", "wegmin_imp1_gr",
    "wegmin_imp2", "wegmin_imp2_gr", "hwzweck2", "zweck_mop", "W_ZWDE", "W_ZWDP", "W_ZWDF",
    "wegkm_gr", "wegkm_imp_gr", "tempo", "tempo_imp", "W_VM_A", "W_VM_B", "W_VM_C", "W_VM_F",
    "W_VM_D", "W_VM_E", "W_VM_G", "W_VM_H", "W_VM_I", "W_VM_J", "W_VM_K", "W_VM_L", "W_VM_M",
    "W_VM_N", "W_VM_O", "W_VM_P", "W_VM_Q", "W_VM_R", "W_VM_S", "W_VM_T", "W_VM_U", "W_VM_Z", "hvm",
    "hvm_diff1", "hvm_diff2", "hvm_oev", "vm_kombi", "weg_intermod", "weg_intermod2", "W_FMF",
    "mop_fmf", "mot_fmf", "pkw_fmf", "lkw_fmf", "W_WAUTO", "W_ANZBEGL", "anzbegl", "anzpers",
    "W_BEGL_HH", "W_BEGL_1", "W_BEGL_2", "W_BEGL_3", "W_BEGL_4", "W_BEGL_5", "W_BEGL_6",
    "W_BEGL_KA", "anzetap", "etapkm", "etapmin", "auto_dist", "auto_dist_gr", "auto_dauer",
    "auto_dauer_vgl", "rad_dist", "rad_dauer", "opnv_dist", "opnv_dist_transit", "opnv_dist_fuss",
    "opnv_dauer_brutto", "opnv_dauer_brutto_vgl", "opnv_dauer_netto", "opnv_dauer_transit",
    "opnv_dauer_fuss", "opnv_dauer_wartezeit", "anzauto_gr3", "H_CS", "hheink_gr2", "mobtyp",
    "alter_gr2", "alter_gr3", "alter_gr4", "alter_gr5", "alter_gr6", "taet_diff", "pergrup1",
    "pergrup2", "bildung", "P_FS_PKW", "vpedrad", "carsharing", "P_STKFZ", "mobein", "seg_vm",
    "multimodal", "BLAND_GEO", "RegioStaR4", "RegioStaRGem5", "XMStadt", "XMStadt_SO", "XMStadt_ZO",
    "MSIndex", "MSIndex_SO", "MSIndex_ZO", "bus28", "bus28_so", "bus28_zo", "tram28", "tram28_so",
    "tram28_zo", "bahn28", "bahn28_so", "bahn28_zo", "min_bab", "min_bab_so", "min_bab_zo",
    "min_ozmz", "min_ozmz_so", "min_ozmz_zo", "haustyp_so", "haustyp_zo", "hausnutz", "hausnutz_so",
    "hausnutz_zo", "wohnlage", "wohnlage_so", "wohnlage_zo", "quali_nv", "quali_nv_so",
    "quali_nv_zo", "quali_opnv", "quali_opnv_so", "quali_opnv_zo",
)


def drop_unused_mid_wege_columns(mid_wege: pd.DataFrame, *, log_tag: str) -> pd.DataFrame:
    """Return a copy of ``mid_wege`` without the :data:`UNUSED_MID_WEGE_COLUMNS` it carries.

    Every other column keeps its position, values and dtype. A listed column the frame does not
    carry is not an error (a unit fixture, or a delivery without that variable): the list says
    which columns MAY be dropped, it is not a schema. The input frame is not modified.

    Logs, at INFO, how many columns were dropped and kept, how many listed columns the frame does
    not carry, and the frame's in-memory size before and after (MiB, ``deep=True``), so every
    run log shows the effect.

    Parameters
    ----------
    mid_wege:
        MiD Wege table, one row per reported leg.
    log_tag:
        The caller's log tag, so the message names which trip build dropped the columns.
    """
    present = [column for column in UNUSED_MID_WEGE_COLUMNS if column in mid_wege.columns]
    narrowed = mid_wege.drop(columns=present)
    n_columns = len(mid_wege.columns)
    logger.info(
        "%s MiD Wege columns no first-party code reads: dropped %d/%d (%.1f%%) before the trip "
        "build, kept %d; %d listed columns not in this frame; Wege frame %.1f -> %.1f MiB "
        "(ADR-0138)",
        log_tag, len(present), n_columns, 100.0 * len(present) / max(n_columns, 1),
        len(narrowed.columns), len(UNUSED_MID_WEGE_COLUMNS) - len(present),
        mid_wege.memory_usage(index=True, deep=True).sum() / _BYTES_PER_MEBIBYTE,
        narrowed.memory_usage(index=True, deep=True).sum() / _BYTES_PER_MEBIBYTE,
    )
    return narrowed

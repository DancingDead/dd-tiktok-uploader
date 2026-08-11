import os
import time
from pathlib import Path

import pytest

import trim_clip
from trim_clip import TRIM_CRF, TRIM_PRESET, coerce_bounds, ffmpeg_trim_args


def test_bornes_nominales():
    assert coerce_bounds(3.0, 20.0, 30.0) == (3.0, 20.0)


def test_les_bornes_acceptent_des_chaines():
    """L'interface envoie du JSON, les nombres peuvent arriver en texte."""
    assert coerce_bounds("3", "20.5", "30") == (3.0, 20.5)


def test_une_fin_qui_depasse_a_peine_est_ramenee_a_la_duree():
    """La derniere frame d'une video tombe rarement pile sur la duree annoncee :
    poser la borne de fin sur la fin du lecteur ne doit pas etre un echec."""
    assert coerce_bounds(0.0, 30.04, 30.0) == (0.0, 30.0)


def test_un_debut_legerement_negatif_est_ramene_a_zero():
    assert coerce_bounds(-0.01, 10.0, 30.0) == (0.0, 10.0)


def test_bornes_inversees():
    with pytest.raises(ValueError):
        coerce_bounds(20.0, 3.0, 30.0)


def test_bornes_egales():
    with pytest.raises(ValueError):
        coerce_bounds(10.0, 10.0, 30.0)


def test_duree_resultante_trop_courte():
    """Sous MIN_TRIMMED_DUR le clip n'a plus de matiere exploitable."""
    with pytest.raises(ValueError):
        coerce_bounds(10.0, 10.5, 30.0)


def test_un_debut_franchement_negatif_est_refuse():
    """Ramener -5 a 0 masquerait une erreur de saisie au lieu de la signaler."""
    with pytest.raises(ValueError):
        coerce_bounds(-5.0, 10.0, 30.0)


def test_une_fin_franchement_au_dela_de_la_duree_est_refusee():
    with pytest.raises(ValueError):
        coerce_bounds(0.0, 45.0, 30.0)


def test_valeurs_non_numeriques():
    with pytest.raises(ValueError):
        coerce_bounds("<script>", 10.0, 30.0)
    with pytest.raises(ValueError):
        coerce_bounds(0.0, None, 30.0)


def test_les_booleens_sont_refuses():
    """float(True) vaut 1.0 : accepter un booleen ferait passer une faute de
    frappe pour une borne valide."""
    with pytest.raises(ValueError):
        coerce_bounds(True, 10.0, 30.0)


def test_valeurs_non_finies():
    with pytest.raises(ValueError):
        coerce_bounds(float("nan"), 10.0, 30.0)
    with pytest.raises(ValueError):
        coerce_bounds(0.0, float("inf"), 30.0)


def test_le_seek_est_avant_l_entree():
    """-ss apres -i ferait decoder toute la video avant la coupe."""
    args = ffmpeg_trim_args(Path("clips/a.mp4"), Path("clips/.trim/a.mp4"), 3.0, 20.0)
    assert args.index("-ss") < args.index("-i")
    assert args.index("-to") < args.index("-i")


def test_les_valeurs_d_encodage_sont_celles_de_la_spec():
    args = ffmpeg_trim_args(Path("a.mp4"), Path("b.mp4"), 0.0, 10.0)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-crf") + 1] == str(TRIM_CRF)
    assert args[args.index("-preset") + 1] == TRIM_PRESET
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-c:a") + 1] == "copy"


def test_les_flags_bitexact_sont_presents():
    """Invariant du projet : sans eux, l'encodeur date le fichier et deux
    encodages identiques different octet a octet. Les TROIS formes sont
    verifiees separement (conteneur, video, audio) : une seule occurrence
    litterale de "+bitexact" laissait passer une implementation qui n'en pose
    qu'une, et le fichier restait date."""
    args = ffmpeg_trim_args(Path("a.mp4"), Path("b.mp4"), 0.0, 10.0)
    for flag in ("-fflags", "-flags:v", "-flags:a"):
        assert flag in args, flag
        assert args[args.index(flag) + 1] == "+bitexact", flag


def test_la_source_et_la_cible_sont_aux_bons_endroits():
    # Chemins attendus via str(Path(...)) et non des littéraux POSIX : sous
    # Windows le séparateur natif est « \ » et ce test échouait sur la tour de
    # prod à chaque exécution, sans que le code soit en cause.
    source, cible = Path("clips/a.mp4"), Path("clips/.trim/a.mp4")
    args = ffmpeg_trim_args(source, cible, 1.0, 5.0)
    assert args[args.index("-i") + 1] == str(source)
    assert args[-1] == str(cible)


# --- Promotion du temporaire : ce qui se joue autour du `replace` ---------------


class _Rc:
    """Retour minimal de subprocess.run : trim_clip ne lit que ces deux champs."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _fake_ffmpeg(ecrit=True):
    """Remplace l'appel ffmpeg : cree (ou non) le fichier de sortie, code 0."""
    def run(argv, **kwargs):
        if ecrit:
            Path(argv[-1]).write_bytes(b"rogne")
        return _Rc(0)
    return run


def _durees(monkeypatch, source, produite):
    """probe_duration truquee : une valeur pour la source, une pour le
    temporaire (reconnu a son dossier .trim/)."""
    def probe(path):
        return produite if Path(path).parent.name == trim_clip.TRIM_DIR_NAME else source
    monkeypatch.setattr(trim_clip, "probe_duration", probe)


def test_un_temporaire_tronque_n_est_pas_promu(tmp_path, monkeypatch):
    """ffmpeg traite une erreur de demuxage en cours de flux comme une fin de
    fichier : il sort en 0 apres n'avoir encode qu'une partie. Promouvoir ce
    fichier detruit definitivement le reste des rushes."""
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"original")
    _durees(monkeypatch, source=200.0, produite=27.44)
    monkeypatch.setattr(trim_clip.subprocess, "run", _fake_ffmpeg())

    with pytest.raises(RuntimeError) as exc:
        trim_clip.trim_clip(clip, 5.0, 180.0, log=lambda m: None)

    assert "illisible" in str(exc.value)
    # Le point qui compte : la matiere premiere est intacte.
    assert clip.read_bytes() == b"original"


def test_un_temporaire_complet_est_promu(tmp_path, monkeypatch):
    """Contre-epreuve : sans elle, un controle de duree trop strict ferait
    echouer TOUS les rognages sans que la suite le dise."""
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"original")
    # 174.8 et non 175.0 : la derniere frame ne tombe jamais pile sur la borne,
    # l'ecart normal doit rester sous END_TOLERANCE.
    _durees(monkeypatch, source=200.0, produite=174.8)
    monkeypatch.setattr(trim_clip.subprocess, "run", _fake_ffmpeg())

    trim_clip.trim_clip(clip, 5.0, 180.0, log=lambda m: None)

    assert clip.read_bytes() == b"rogne"
    assert list((tmp_path / trim_clip.TRIM_DIR_NAME).glob("*")) == []


def test_un_replace_refuse_conserve_le_temporaire(tmp_path, monkeypatch):
    """Sous Windows c'est le mode d'echec attendu (antivirus, indexeur, handle
    ouvert) et le seul ou le fichier produit est bon : le jeter obligerait a
    recommencer deux minutes d'encodage pour rien."""
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"original")
    _durees(monkeypatch, source=200.0, produite=174.8)
    monkeypatch.setattr(trim_clip.subprocess, "run", _fake_ffmpeg())

    def refuse(self, target):
        raise PermissionError("fichier utilise par un autre processus")
    monkeypatch.setattr(Path, "replace", refuse)

    with pytest.raises(RuntimeError) as exc:
        trim_clip.trim_clip(clip, 5.0, 180.0, log=lambda m: None)

    restants = list((tmp_path / trim_clip.TRIM_DIR_NAME).glob("*"))
    assert len(restants) == 1
    # Le chemin doit etre NOMME : un temporaire garde mais introuvable ne sert
    # a rien a l'utilisateur.
    assert str(restants[0]) in str(exc.value)
    assert clip.read_bytes() == b"original"


def test_un_echec_ffmpeg_supprime_le_temporaire(tmp_path, monkeypatch):
    """L'autre face de la regle : un fichier partiel n'a aucune valeur, le
    garder ne ferait qu'accumuler des centaines de Mo invisibles."""
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"original")
    _durees(monkeypatch, source=200.0, produite=174.8)

    def echoue(argv, **kwargs):
        Path(argv[-1]).write_bytes(b"partiel")
        return _Rc(234, "erreur ffmpeg")
    monkeypatch.setattr(trim_clip.subprocess, "run", echoue)

    with pytest.raises(RuntimeError):
        trim_clip.trim_clip(clip, 5.0, 180.0, log=lambda m: None)

    assert list((tmp_path / trim_clip.TRIM_DIR_NAME).glob("*")) == []
    assert clip.read_bytes() == b"original"


# --- Nettoyage de .trim/ --------------------------------------------------------


def test_purge_supprime_les_temporaires_morts_et_garde_les_recents(tmp_path):
    """Un kill -9 ou une coupure de courant laisse dans .trim/ un mp4 partiel
    de plusieurs centaines de Mo, dans un dossier qu'aucun ecran ne montre."""
    temp_dir = tmp_path / ".trim"
    temp_dir.mkdir()
    vieux = temp_dir / "a.1234.mp4"
    vieux.write_bytes(b"mort")
    recent = temp_dir / "b.5678.mp4"
    recent.write_bytes(b"en cours")
    maintenant = 1_000_000.0
    os.utime(vieux, (maintenant - 25 * 3600, maintenant - 25 * 3600))
    os.utime(recent, (maintenant - 60, maintenant - 60))

    purges = trim_clip.purge_temporaires(temp_dir, now=maintenant, log=lambda m: None)

    assert purges == ["a.1234.mp4"]
    assert not vieux.exists()
    # Un temporaire recent peut etre celui d'un rognage EN COURS, y compris
    # dans un autre process : le supprimer detruirait son travail.
    assert recent.exists()


def test_le_rognage_nettoie_les_temporaires_morts(tmp_path, monkeypatch):
    """Le nettoyage doit etre sur le chemin du rognage, pas seulement
    disponible : personne n'ira jamais l'appeler a la main."""
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"original")
    temp_dir = tmp_path / ".trim"
    temp_dir.mkdir()
    mort = temp_dir / "vieux.1.mp4"
    mort.write_bytes(b"mort")
    vieille_date = time.time() - 48 * 3600
    os.utime(mort, (vieille_date, vieille_date))
    _durees(monkeypatch, source=200.0, produite=174.8)
    monkeypatch.setattr(trim_clip.subprocess, "run", _fake_ffmpeg())

    trim_clip.trim_clip(clip, 5.0, 180.0, log=lambda m: None)

    assert not mort.exists()

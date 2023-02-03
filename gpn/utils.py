import gzip
from Bio import SeqIO, bgzf
from Bio.Seq import Seq
import bioframe as bf
import numpy as np
import pandas as pd
from tqdm import tqdm
tqdm.pandas()


DEFINED_SYMBOLS = list("ACGTacgt")
UNMASKED_SYMBOLS = list("ACGT")


def load_fasta(path):
    with gzip.open(path, "rt") if path.endswith(".gz") else open(path) as handle:
        genome = SeqIO.to_dict(SeqIO.parse(handle, "fasta"))
    genome = pd.Series({c: str(rec.seq) for c, rec in genome.items()})
    return genome


def save_fasta(path, genome):
    with bgzf.BgzfWriter(path, "wb") if path.endswith(".gz") else open(path, "w") as handle:
        SeqIO.write(genome.values(), handle, "fasta")


# Some standard formats
def load_table(path):
    if path.endswith('.parquet'):
        df = pd.read_parquet(path)
    elif 'csv' in path:
        df = pd.read_csv(path)
    elif 'tsv' in path:
        df = pd.read_csv(path, sep='\t')
    elif 'vcf' in path:
        df = pd.read_csv(
            path, sep="\t", header=None, comment="#", usecols=[0,1,2,3,4],
        ).rename(cols={0: 'chrom', 1: 'pos', 2: 'id', 3: 'ref', 4: 'alt'})
        df.pos -= 1
    elif 'gtf' in path or 'gff' in path:
        df = pd.read_csv(
            path,
            sep="\t",
            header=None,
            comment="#",
            names=[
                "chrom",
                "source",
                "feature",
                "start",
                "end",
                "score",
                "strand",
                "frame",
                "attribute",
            ],
        )
    df.chrom = df.chrom.astype(str)
    return df


def load_repeatmasker(path):
    df = pd.read_csv(path, sep="\t").rename(
        columns=dict(genoName="chrom", genoStart="start", genoEnd="end")
    )
    df.chrom = df.chrom.astype(str)
    return df


class Genome:
    def __init__(self, path):
        self._genome = load_fasta(path)

    def get_seq(self, chrom, start, end, strand="+"):
        seq = self._genome[chrom][start:end]
        if strand == "-":
            seq = str(Seq(seq).reverse_complement())
        return seq

    def filter_chroms(self, chroms):
        self._genome = self._genome[chroms]

    def get_seq_fwd_rev(self, chrom, start, end):
        seq_fwd = self.get_seq(chrom, start, end)
        seq_rev = str(Seq(seq_fwd).reverse_complement())
        return seq_fwd, seq_rev

    def get_all_intervals(self):
        return pd.DataFrame([
            {"chrom": chrom, "start": 0, "end": len(seq)}
            for chrom, seq in self._genome.items()
        ])

    def get_intervals_matching_symbols(self, symbols):
        def get_intervals_matching_symbols_chrom(chrom):
            intervals = pd.DataFrame(dict(
                start=np.where(np.isin(list(chrom.seq), symbols))[0]
            ))
            intervals["chrom"] = chrom.name
            intervals["end"] = intervals.start + 1
            return bf.merge(intervals).drop(columns="n_intervals")

        return pd.concat(
            self._genome.rename("seq").to_frame().progress_apply(
                get_intervals_matching_symbols_chrom, axis=1,
            ).values,
            ignore_index=True,
        )

    def get_defined_intervals(self):
        return self.get_intervals_matching_symbols(DEFINED_SYMBOLS)

    def get_unmasked_intervals(self):
        return self.get_intervals_matching_symbols(UNMASKED_SYMBOLS)


def add_space_every_k(seq, k):
    return " ".join([seq[x:x+k] for x in range(0, len(seq), k)])

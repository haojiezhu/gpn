from Bio import SeqIO
from Bio.Seq import Seq
import numpy as np
import pandas as pd
import torch
from transformers import AutoTokenizer, Trainer, TrainingArguments

from plantbert.chromatin.model import PlantBertModel


variants_path = "../../data/variants/filt.parquet"
genome_path = "../../data/tair10.fa"
tokenizer_path = "../mlm/results/checkpoint-200000/"
max_length = 200
window_size = 1000
model_ckpt = "version_6/checkpoints/epoch=9-step=33449.ckpt"
output_path = "vep.parquet"
output_dir = "results_vep"  # not really used but necessary for trainer


# TODO: should load both genome and tokenizer later, to avoid memory leak with num_workers>0
class VEPDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        variants_path=None,
        genome_path=None,
        tokenizer_path=None,
        max_length=None,
        window_size=None,
    ):
        self.variants_path = variants_path
        self.genome_path = genome_path
        self.tokenizer_path = tokenizer_path
        self.max_length = max_length
        self.window_size = window_size

        self.variants = pd.read_parquet(self.variants_path)
        #self.variants = self.variants.head(10000)

        df_ref_pos = self.variants.copy()
        df_ref_pos["start"] = df_ref_pos.pos - self.window_size // 2
        df_ref_pos["end"] = df_ref_pos.start + self.window_size
        df_ref_pos["strand"] = "+"
        df_ref_pos["status"] = "ref"
        df_ref_neg = df_ref_pos.copy()
        df_ref_neg.strand = "-"
        df_alt_pos = df_ref_pos.copy()
        df_alt_pos.status = "alt"
        df_alt_neg = df_alt_pos.copy()
        df_alt_neg.strand = "-"
        self.df = pd.concat(
            [df_ref_pos, df_ref_neg, df_alt_pos, df_alt_neg], ignore_index=True
        )
        # TODO: might consider interleaving this so the first 4 rows correspond to first variant, etc.
        # can sort_values to accomplish that, I guess
        self.genome = SeqIO.to_dict(SeqIO.parse(self.genome_path, "fasta"))

        self.tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_path)

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        seq = self.genome[row.chromosome][row.start : row.end].seq
        assert len(seq) == self.window_size
        assert seq[self.window_size//2] == row.ref

        if row.status == "alt":
            seq_list = list(str(seq))
            seq_list[self.window_size//2] = row.alt
            seq = Seq("".join(seq_list))

        if row.strand == "-":
            seq = seq.reverse_complement()
        seq = str(seq)

        nucleotides = pd.unique(list(seq))
        assert len(nucleotides) == 4 and "A" in nucleotides and "C" in nucleotides and "G" in nucleotides and "T" in nucleotides

        x = self.tokenizer(
            seq,
            padding="max_length",
            max_length=self.max_length,
            return_token_type_ids=False,
            return_tensors="pt",
            truncation=True,
        )
        x["input_ids"] = x["input_ids"].flatten()
        x["attention_mask"] = x["attention_mask"].flatten()
        return x


d = VEPDataset(
    variants_path=variants_path,
    genome_path=genome_path,
    tokenizer_path=tokenizer_path,
    max_length=max_length,
    window_size=window_size,
)

model = PlantBertModel.load_from_checkpoint(model_ckpt, language_model_path=tokenizer_path)

training_args = TrainingArguments(
    output_dir=output_dir,
    per_device_eval_batch_size=512,
    dataloader_num_workers=0,
)

trainer = Trainer(
    model=model,
    args=training_args,
)

pred = trainer.predict(test_dataset=d).predictions
print(pred.shape)

n_variants = len(d.variants)
pred_ref_pos = pred[0*n_variants:1*n_variants]
pred_ref_neg = pred[1*n_variants:2*n_variants]
pred_alt_pos = pred[2*n_variants:3*n_variants]
pred_alt_neg = pred[3*n_variants:4*n_variants]

pred_ref = np.stack((pred_ref_pos, pred_ref_neg)).mean(axis=0)
pred_alt = np.stack((pred_alt_pos, pred_alt_neg)).mean(axis=0)
delta_pred = pred_alt - pred_ref

variants = d.variants
variants.loc[:, model.feature_names] = delta_pred
print(variants)
variants.to_parquet(output_path, index=False)

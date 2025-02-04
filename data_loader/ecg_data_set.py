import os
import pickle
import pickle as pk
from pathlib import Path

import pandas as pd
import torch
from torch.utils.data import Dataset

from utils import get_project_root, ensure_dir


def _save_record_names_to_txt(mode, record_names, suffix):
    project_root = get_project_root()
    ensure_dir(os.path.join(project_root, 'data_loader', 'log'))
    with open(os.path.join(project_root, 'data_loader', 'log', f'Record_names_{mode}_{suffix}.txt'), "w+") as txt_file:
        for line in sorted(record_names):
            txt_file.write("".join(line) + "\n")


class ECGDataset(Dataset):
    """
    ECG dataset
    Read the record names in __init__ but leaves the reading of actual data to __getitem__.
    This is memory efficient because all the records are not stored in the memory at once but read as required.
    """

    def __init__(self, input_dir):
        """

        :param input_dir: Path  -> Path to the directory containing the preprocessed pickle files for each record
        :param transform: callable, optional -> Optional transform(s) to be applied on a sample.
        """

        records = []
        for file in sorted(os.listdir(input_dir)):
            if ".pk" not in file:
                continue
            records.append(file)

        self._input_dir = input_dir
        self.records = records
        # Save list of classes occurring in the dataset
        _, meta = pk.load(open(os.path.join(self._input_dir, records[0]), "rb"))
        self.class_labels = meta["classes_one_hot"].index.values

    def __len__(self):
        return len(self.records)

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()

        record_name = self.records[idx]
        # record is a df, meta a dict
        record, meta = pk.load(open(os.path.join(self._input_dir, record_name), "rb"))
        # Ensure that the record is not containing any unknown class label
        assert all(label in self.class_labels for label in meta["classes_encoded"])

        return record.values.astype("float32"), \
            str(meta["classes_encoded"]), meta["classes_encoded"][0], \
            meta["classes_one_hot"].values, record_name

    def get_ml_pos_weights(self, idx_list, mode=None, cross_valid_active=False):
        """
        Calculates positive weights for multi-label classification.
        Used for weighted BCE loss to trade off recall and precision by adding weights to positive examples.
        From Pytorch Doc: if a dataset contains 100 positive and 300 negative examples of a single class, then
        pos_weight for the class should be equal to 300/100=3
        :@param idx_list: list of ids, should contain all ids contained in the train, valid or test set
        :@param mode: should be 'train' or 'valid' or 'test'
        :@param cross_valid_active: Set to True during cross validation to ensure weights are re-calculated each run
        :@return:  Pos weights, one weight per class

        """

        if mode == "test" and "test" not in self._input_dir:
            # Catch the case where the model is tested on the validation set during development
            # (not used for final eval)
            mode = "valid"

        if str(get_project_root()) in self._input_dir:
            relative_path = os.path.relpath(self._input_dir, get_project_root())
        else:
            relative_path = self._input_dir
        dataset = relative_path.split("/")[1]
        suffix = f"{dataset}" if dataset == "CinC_CPSC" \
            else f"{dataset}_{relative_path.split('/')[2].split('_')[0]}"

        file_name = os.path.join(get_project_root(), f"data_loader/log/pos_weights_ml_{mode}_{suffix}.p")

        # For cross-validation, statistics change from run to run!
        if not cross_valid_active and os.path.isfile(file_name):

            # File has already been created. For safety, ensure that it fits to the given idx_list!
            self._consistency_check_data_split(idx_list, mode, f"pos_weights_{suffix}")

            # If the file exists and the required indices match, just load the dataframe
            with open(file_name, "rb") as file:
                df = pickle.load(file)
        else:
            # File has not yet been created or cross validation is active
            # => Determine information from scratch and, in cases w/o cross validation, save to file
            classes = []
            record_names = []
            for idx in idx_list:
                _, _, _, classes_one_hot, record_name = self.__getitem__(idx)
                classes.append(classes_one_hot)
                record_names.append(record_name)

            if mode is not None and not cross_valid_active:
                # Dump the record names to a txt file to ensure they are the same between VMs
                _save_record_names_to_txt(mode, record_names, f"pos_weights_{suffix}")

            # Get the class freqs as Pandas series
            class_freqs = pd.DataFrame(classes).sum()

            # Calculate the number of pos and negative samples per class
            df = pd.DataFrame({'num_pos_samples': class_freqs})

            # Each class should occur at least ones
            assert not df['num_pos_samples'].isin([0]).any(), "Each class should occur at least ones"

            df['num_neg_samples'] = df.apply(lambda row: len(idx_list) - row.values).values
            df["ratio_neg_to_pos"] = df.num_neg_samples / (df.num_pos_samples)
            # If num_pos_samples can be 0, a dummy term needs to be added to it to avoid dividing by 0
            # df["ratio_neg_to_pos"] = df.num_neg_samples / (df.num_pos_samples + 1e-5)

            if mode is not None and not cross_valid_active:
                # Save the pos_weights to a pickle file called pos_weights_ml_{mode}}.p,
                # the corresponding file names were already saved to Record_names_{mode}_pos_weights.txt
                with open(Path(file_name), "wb") as file:
                    pickle.dump(df, file)

        # Return the ratio as as ndarray
        return df["ratio_neg_to_pos"].values

    def get_inverse_class_frequency(self, idx_list, multi_label_training, mode, cross_valid_active=False):
        """
        Can be used to determine the inverse class frequencies
        :param idx_list: list of ids, should contain all ids contained in the train, valid or test set
        :param multi_label_training: If true, all labels are considered, otherwise only the first label is counted
        :param cross_valid_active: Set to True during cross validation to ensure weights are re-calculated each run
        :return:  Inverse class frequencies
        """

        return self.get_class_frequency(idx_list, multi_label_training, mode,
                                        inverse=True,
                                        cross_valid_active=cross_valid_active)

    def get_class_frequency(self, idx_list, multi_label_training, mode, inverse=False, cross_valid_active=False):
        """
        Can be used to determine the (inverse) class frequencies for either multi-label or single-label classification.
        Both the class freqs and the inverse class freqs are read from or written to the same dataframe
        :@param idx_list: list of ids, should contain all ids contained in the train, valid or test set
        :@param multi_label_training: If true, all labels are considered, otherwise only the first label is counted
        :@param mode: should be 'train' or 'valid' or 'test'
        :@param inverse: If true, inverse class freqs are returned
        :@param cross_valid_active: Set to True during cross validation to ensure weights are re-calculated each run
        :@return:  (Inverse) class frequencies
        """

        if mode == "test" and "test" not in self._input_dir:
            # Catch the case where the model is tested on the validation set during development
            # (not used for final eval)
            mode = "valid"

        if str(get_project_root()) in self._input_dir:
            relative_path = os.path.relpath(self._input_dir, get_project_root())
        else:
            relative_path = self._input_dir
        dataset = relative_path.split("/")[1]
        suffix = f"{dataset}" if dataset == "CinC_CPSC" \
            else f"{dataset}_{relative_path.split('/')[2].split('_')[0]}"

        print(f"Using suffix {suffix}")

        file_name = f"data_loader/log/class_freqs_ml_{mode}_{suffix}.p" if multi_label_training \
            else f"data_loader/log/class_freqs_sl_{mode}_{suffix}.p"
        file_name = os.path.join(get_project_root(), file_name)

        # For cross-validation, statistics change from run to run!
        if not cross_valid_active and os.path.isfile(file_name):

            # File has already been created. For safety, ensure that it fits to the given idx_list!
            self._consistency_check_data_split(idx_list, mode, f"class_freqs_{suffix}")

            # If the file exists and the required indices match, just load the dataframe
            with open(file_name, "rb") as file:
                df = pickle.load(file)
            class_freqs = df['Class_freq']
            inverse_class_freqs = df['Inverse_class_freq']
        else:
            # File has not yet been created or cross validation is active
            # => Determine information from scratch and, in cases w/o cross validation, save to file
            classes = []
            record_names = []
            for idx in idx_list:
                _, _, first_class_encoded, classes_one_hot, record_name = self.__getitem__(idx)
                record_names.append(record_name)
                if multi_label_training:
                    classes.append(classes_one_hot)
                else:
                    # Only consider the first label
                    classes_one_hot[:] = 0
                    classes_one_hot[first_class_encoded] = 1
                    classes.append(classes_one_hot)

            if mode is not None and not cross_valid_active:
                # Dump the record names to a txt file to ensure they are the same between VMs
                _save_record_names_to_txt(mode, record_names, f"class_freqs_{suffix}")

            # Get the class freqs as Pandas series
            class_freqs = pd.DataFrame(classes).sum()

            # Each class should occur at least ones
            assert not class_freqs.isin([0]).any(), "Each class should occur at least ones"

            # Calculate the inverse class freqs
            inverse_class_freqs = class_freqs.apply(lambda x: class_freqs.sum() / x)

            df = pd.concat([class_freqs, inverse_class_freqs], axis=1)
            df.columns = ['Class_freq', 'Inverse_class_freq']

            if mode is not None and not cross_valid_active:
                # Save the class_freqs to a pickle file called inverse_class_freq_<sl or ml>_{mode}.p,
                # the corresponding file names were already saved to Record_names_{mode}_class_freqs.txt
                with open(Path(file_name), "wb") as file:
                    pickle.dump(df, file)

        # Return them as as ndarray
        return class_freqs.values if not inverse else inverse_class_freqs.values

    def _consistency_check_data_split(self, idx_list, mode, suffix):
        with open(os.path.join(get_project_root(), f"data_loader/log/Record_names_{mode}_{suffix}.txt"), "r") as file:
            records_for_mode = [line.rstrip() for line in file]
            current_records = []
            for idx in idx_list:
                _, _, _, _, record_name = self.__getitem__(idx)
                current_records.append(record_name)

        # with open(os.path.join(get_project_root(), f"data_loader/log/DEBUG_{mode}_{suffix}.txt"), "w") as txt_file:
        #     txt_file.write(f"Check called for {f'data_loader/log/Record_names_{mode}_{suffix}.txt'}" + "\n")
        #     txt_file.write("CURRENT RECORDS:" + "\n")
        #     for line in sorted(current_records):
        #         txt_file.write("".join(line) + "\n")
        #     txt_file.write("\n" + "\n" + "\n" + "DESIRED RECORDS:" + "\n")
        #     for line in sorted(records_for_mode):
        #         txt_file.write("".join(line) + "\n")

        assert sorted(current_records) == sorted(records_for_mode), "Data Split Error! Check this again!"


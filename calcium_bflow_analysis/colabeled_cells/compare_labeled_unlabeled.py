import pathlib
from typing import List, Tuple, Union

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import attr
from attr.validators import instance_of, optional

from calcium_bflow_analysis.dff_analysis_and_plotting.dff_analysis import (
    locate_spikes_peakutils,
    scatter_spikes,
)


@attr.s
class ShowLabeledAndUnlabeled:
    """
    Plots a simple comparison of the dF/F traces that
    originated from the unlabeled cells and the labeled
    cells.
    """

    foldername = attr.ib(validator=instance_of(pathlib.Path))
    fps = attr.ib(default=58.2, validator=instance_of(float))
    file_pairs = attr.ib(init=False)
    indices_per_fov = attr.ib(init=False)

    def run(self):
        """ Main pipeline """
        self.file_pairs = self._find_results_and_colabeled_files(self.foldername)
        self.indices_per_fov = self._find_indices_for_labeled_unlabeled_data(
            self.file_pairs
        )
        max_shape = self._find_max_shape(self.file_pairs)
        labeled, unlabeled = self._stack_dff_arrays(self.file_pairs, max_shape)
        fig = plt.figure(figsize=(20, 16))
        gs = gridspec.GridSpec(max_shape[0], 16, figure=fig)
        self._populate_fig_rows(gs, labeled, unlabeled, self.fps, self.indices_per_fov, max_shape)

        self._plot_against(labeled, unlabeled, self.fps, self.indices_per_fov)

    def _find_results_and_colabeled_files(self, folder):
        """
        Populate a DataFrame with pairs of results filenames and the
        "colabeled_idx.npy" filenames, which contain the indices of
        the cells that are also labeled.
        """
        file_pairs = pd.DataFrame({"results": [], "colabeled": []})
        for result_file in folder.rglob("*results.npz"):
            try:
                name = result_file.name[:-11] + "colabeled_idx.npy"
                colabel_file = next(result_file.parent.glob(name))
            except StopIteration:
                continue
            else:
                pair = pd.Series({"results": result_file, "colabeled": colabel_file})
                file_pairs = file_pairs.append(pair, ignore_index=True)
        return file_pairs

    def _find_indices_for_labeled_unlabeled_data(self, file_pairs):
        """
        Populate the DataFrame containing the file names with the
        indices of the cells which belong to each group - labeled
        and unlabeled.
        """
        indices_df = pd.DataFrame({"labeled_idx": [], "unlabeled_idx": []})
        for idx, file in file_pairs.iterrows():
            all_idx = np.arange(np.load(file["results"])["F_dff"].shape[0])
            labeled_idx = np.load(file["colabeled"])
            unlabeled_idx = np.delete(all_idx, labeled_idx, axis=0)
            cur_indices = pd.Series(
                {"labeled_idx": [labeled_idx], "unlabeled_idx": [unlabeled_idx]}
            )
            indices_df = indices_df.append(cur_indices, ignore_index=True)
        return indices_df

    def _stack_dff_arrays(
        self, file_pairs, max_shape: tuple
    ) -> Tuple[List[np.ndarray], List[np.ndarray]]:
        """
        Loads each pair of dF/F data and colabeled cells and
        separates the labeled and the unlabeled cells.
        """
        labeled_data = np.zeros(max_shape)
        unlabeled_data = np.zeros(max_shape)
        row_label = 0
        row_unlabel = 0
        for _, file in file_pairs.iterrows():
            all_data = np.load(file["results"])["F_dff"]
            labeled_idx = np.load(file["colabeled"])
            labeled = all_data[labeled_idx, :]
            unlabeled = np.delete(all_data, labeled_idx, axis=0)
            num_of_label_rows = labeled.shape[0]
            num_of_unlabel_rows = unlabeled.shape[0]
            labeled_data[
                row_label : row_label + num_of_label_rows, : labeled.shape[1]
            ] = labeled
            unlabeled_data[
                row_unlabel : row_unlabel + num_of_unlabel_rows, : unlabeled.shape[1]
            ] = unlabeled
            row_label += num_of_label_rows
            row_unlabel += num_of_unlabel_rows

        return labeled_data, unlabeled_data

    def _find_max_shape(self, file_pairs):
        """
        Iterate over the found files and decide upon the shape of
        the array that will hold the stacked data. This is useful
        when the number of measurements in each FOV was unequal.
        """
        shapes = []
        num_of_labeled = 0
        for _, file in file_pairs.iterrows():
            all_data = np.load(file["results"])["F_dff"]
            num_of_labeled += np.load(file["colabeled"]).shape[0]
            shapes.append(all_data.shape)

        shapes = np.array(shapes)
        num_of_rows = shapes[:, 0].sum() - num_of_labeled
        max_cols = shapes[:, 1].max()
        return num_of_rows, max_cols

    def _plot_against(self, labeled, unlabeled, fps, indices_per_fov):
        """
        Plot one against the other the labeled and unlabeled
        traces.
        TODO: ADD THE CELL EXCERPTS
        """
        fig, ax = plt.subplots(1, 2, sharey=True)
        spikes_labeled = locate_spikes_peakutils(labeled, fps=fps)
        spikes_unlabeled = locate_spikes_peakutils(unlabeled, fps=fps)
        x_ax = np.arange(labeled.shape[1]) / fps
        scatter_spikes(
            labeled, spikes_labeled, downsample_display=1, time_vec=x_ax, ax=ax[1]
        )
        scatter_spikes(
            unlabeled, spikes_unlabeled, downsample_display=1, time_vec=x_ax, ax=ax[0]
        )
        fig.suptitle(
            f"Comparison of PNN-negative neurons (left) and positive from 3 FOVs"
        )
        ax[0].set_title("Non-PNN neurons")
        ax[1].set_title("PNN-labeled neurons")

    def _populate_fig_rows(self, gs: gridspec.GridSpec, labeled, unlabeled, fps, indices_per_fov, max_shape):
        """

        """
        for row in range(max_shape[0]):
            ax_img_unlabeled = plt.subplot(gs[row, 0])
            ax_trace_unlabeled = plt.subplot(gs[row, 1:gs.get_geometry()[1]//2])
            self._populate_fig_row(row, ax_img_unlabeled, ax_trace_unlabeled, unlabeled, fps, )


@attr.s
class FovSubsetData:
    """
    A dataclass-like object keeping tabs of data for a subset of data
    in a given FOV. Used, for example, when a FOV has both labeled
    and unlabeled cells. In this case, a FovData instance will contain
    two FovSubsetData instances.

    Parameters:
    :param pathlib.Path results_file: an .npz file generated by CaImAn
    :param Union[NoneType, bool] with_labeling: Controls whether the data
    was taken with a second channel containing morphological data. True
    means that this subset points to the labeled data, False means that
    this subset points to the unlabeled data, and None means that there was
    no colabeling involved with this data.
    """
    results_file = attr.ib(validator=instance_of(pathlib.Path))
    with_labeling = attr.ib(validator=optional(instance_of(bool)))
    tif_file = attr.ib(init=False)
    colabel_file = attr.ib(init=False)
    dff = attr.ib(init=False, repr=False)
    indices = attr.ib(init=False, repr=False)

    def load_data(self):
        """ Main class method to populate its different
        attributes with the data and proper files """
        self.tif_file = self._find_tif_file()
        if self.with_labeling is not None:
            self.colabel_file = self._find_colabeled_file()
        self.dff, self.indices = self._populate_dff_data()

    def _find_tif_file(self):
        """
        Finds and returns the associated tif file. Returns None if
        doesn't exist.
        """
        name = self.results_file.name[:-11] + ".tif"
        try:
            tif_file = next(self.results_file.parent.glob(name))
            return tif_file
        except StopIteration:
            return None

    def _find_colabeled_file(self) -> Union[pathlib.Path, None]:
        """
        Finds and returns the colabeled file. Returns None if
        doesn't exist.
        """
        name = self.results_file.name[:-11] + "colabeled_idx.npy"
        try:
            colabel_file = next(self.results_file.parent.glob(name))
            return colabel_file
        except StopIteration:
            return None

    def _populate_dff_data(self):
        """
        Using the different found filenames, load the dF/F data into
        memory. If a subset of the rows should be loaded (since we're
        working with labeled data) the indices of the relevant
        rows are also returned.
        """
        all_data = np.load(self.results_file)['F_dff']
        if self.with_labeling is None:
            return all_data, slice(None)

        indices = np.load(self.colabel_file)
        if self.with_labeling:
            return all_data[indices], indices
        if not self.with_labeling:
            all_indices = np.arange(all_data.shape[0])
            remaining_indices = np.delete(all_indices, indices)
            remaining_traces = all_data[remaining_indices]
            return remaining_traces, remaining_indices


@attr.s
class FovDataContainer:
    """
    A dataclass-like object which holds data taken from a specific FOV
    in its two subsets, or variations, i.e. labeled and unlabeled
    cells.
    """
    results_file = attr.ib(validator=instance_of(pathlib.Path))
    labeled = attr.ib(validator=instance_of(FovSubsetData), repr=False)
    unlabeled = attr.ib(validator=instance_of(FovSubsetData), repr=False)
    tif = attr.ib(init=False)



if __name__ == "__main__":
    foldername = pathlib.Path("/data/Amit_QNAP/ForHagai")
    fovs = []
    for res_file in foldername.rglob('*results.npz'):
        subset_with = FovSubsetData(res_file, with_labeling=True)
        subset_with.load_data()
        subset_without = FovSubsetData(res_file, with_labeling=False)
        subset_without.load_data()
        fovs.append(FovDataContainer(res_file, subset_with, subset_without))



    # showl = ShowLabeledAndUnlabeled(foldername, fps=58.2)
    # showl.run()
    # plt.show()


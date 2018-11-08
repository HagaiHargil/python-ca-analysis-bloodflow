import pathlib

import numpy as np
import pandas as pd
import xarray as xr
from ansimarkup import ansiprint as aprint
import peakutils
import matplotlib
import matplotlib.gridspec
import matplotlib.pyplot as plt
from matplotlib import patches
from matplotlib import gridspec
import sklearn.metrics
import skimage.draw
import tifffile
import scipy.ndimage

from ca_analysis import caiman_funcs_for_comparison
from ca_analysis.find_colabeled_cells import TiffChannels


def calc_dff(file) -> np.ndarray:
    """ Read the dF/F data from a specific file. If the data doesn't exist,
    caclulate it using CaImAn's function.
    """
    data = np.load(file)
    print(f"Analyzing {file}...")
    try:
        dff =  data['F_dff']
    except KeyError:
        dff =  caiman_funcs_for_comparison.detrend_df_f_auto(data['A'], data['b'], data['C'],
                                                                data['f'], data['YrA'])
    finally:
        aprint(f"The shape of the <b>dF/F matrix</b> for file <i>{file}</i> is <yellow>{dff.shape}</yellow>.")

    return dff


def calc_dff_batch(files):
    """ Read data from a sequence of files """
    all_data = []
    for file in files:
        all_data.append(calc_dff(file))
    return np.concatenate(all_data)


def locate_spikes_peakutils(data, fps=30.03, thresh=0.65):
    """ 
    Find spikes from a dF/F matrix using the peakutils package.
    The fps parameter is used to calculate the minimum allowed distance \
    between consecutive spikes. 
    """
    assert len(data.shape) == 2 and data.shape[0] > 0
    all_spikes = np.zeros_like(data)
    min_dist = int(fps)
    for row, cell in enumerate(data):
        peaks = peakutils.indexes(cell, thres=thresh, min_dist=min_dist)
        all_spikes[row, peaks] = 1
    
    return all_spikes


def calc_mean_spike_num(data, fps=30.03, thresh=0.75):
    """
    Find the spikes in the data (using "locate_spikes_peakutils") and count
    them, to create statistics on their average number.
    :param data: Raw data, cells x time
    :param fps: Framerate
    :param thresh: Peakutils threshold for spikes
    :return: Number of spikes for each neuron
    """
    all_spikes = locate_spikes_peakutils(data, fps, thresh)
    mean_of_spikes = all_spikes.sum(axis=1) / data.shape[1]
    return mean_of_spikes


def scatter_spikes(raw_data, spike_data, downsample_display=10, time_vec=None):
    """
    Shows a scatter plots of spike locations on each individual fluorescent trace.
    Parameters:
        raw_data (np.ndarray): The original fluorescent traces matrix, cell x time.
        spike_data (np.ndarray): The result of the `locate_spikes` function, a matrix
                                 with 1 wherever a spike was detected, and 0 otherwise.
        downsample_display (int): Too many cells create clutter and are hard to display.
                                  This is the downsampling factor.
        time_vec (np.ndarray): 1D array with the x-axis values (time). If None, will
                               use simple range(0, max) integer values.
    """
    if time_vec is None:
        time_vec = np.arange(raw_data.shape[1])
    x, y = np.nonzero(spike_data)
    fig, ax = plt.subplots()
    downsampled_data = raw_data[::downsample_display]
    num_displayed_cells = downsampled_data.shape[0]
    ax.plot(time_vec,
            (downsampled_data + np.arange(num_displayed_cells)[:, np.newaxis]).T)
    peakvals = raw_data * spike_data
    peakvals[peakvals == 0] = np.nan
    ax.plot(time_vec,
            (peakvals[::downsample_display] + np.arange(num_displayed_cells)[:, np.newaxis]).T,
            'r.', linewidth=0.1)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('Cell ID')
    return fig, num_displayed_cells


def plot_mean_vals(data, x_axis=None, window=30, title='Rolling Mean',
                   ax=None) -> matplotlib.axes.Axes:
    """ 
    Calculate a mean rolling window on the data, after averaging the 0th (cell) axis.
    This can be used to calculate the rolling mean firing rate, if `data` is a 0-1 binary 
    matrix containing spike locations, or the rolling mean dF/F value if `data` contains
    the raw dF/F values for all cells.
    Parameters:
        data (np.ndarray): Data to be rolling-windowed.
        x_axis (np.ndarray): 1D array of time points for display purposes.
        window (int): size of rolling window in number of array cells.
        title (str): Title of the figure.
        ax (plt.Axis): Axis to plot on. If None - creates a new one.
    
    Returns:
        ax (plt.Axis): Axis that was plotted on.
        mean (float): The mean value of the entire rolling data.
    """
    if x_axis is None:
        x_axis = np.arange(data.shape[1])
    mean = pd.DataFrame(data.mean(axis=0))
    mean['x'] = x_axis
    mean_val = mean.rolling(window=window).mean()
    ax = mean_val.plot(x='x', ax=ax)
    ax.set_xlabel('Time')
    ax.set_ylabel('Mean rate')
    ax.set_title(title)
    return ax, mean_val[0].mean()


def calc_auc(data, norm_factor=1):
    """ Return the normalized area under the curve of all neurons in the data matrix.
    Uses a simple trapezoidal rule, and subtracts the offset of each cell before 
    the computation.
    """
    x = np.arange(data.shape[1])
    all_auc = []
    for cell in data:
        no_offset = cell - cell.min()
        auc = sklearn.metrics.auc(x, no_offset)
        result = auc / (data.shape[1] * norm_factor)
        all_auc.append(result)
    all_auc = np.array(all_auc) 
    return all_auc

def calc_mean_dff(data):
    """ Return the mean dF/F value, and the SEM of all neurons in the data matrix.
    Subtracts the offset of each cell before the computation.
    """
    min_vec = np.atleast_2d(data.min(axis=1)).T
    data_no_offset = data - min_vec
    return data_no_offset.mean(1)


def display_heatmap(data, ax=None, epoch='All cells', downsample_factor=8,
                    fps=30.03):
    """ Show an "image" of the dF/F of all cells """
    if not ax:
        _, ax = plt.subplots()
    downsampled = data[::downsample_factor, ::downsample_factor].copy()
    top = np.nanpercentile(downsampled, q=95)
    bot = np.nanpercentile(downsampled, q=5)
    try:
        xaxis = np.arange(downsampled.shape[1]) * downsample_factor / fps
        yaxis = np.arange(downsampled.shape[0])
        ax.pcolor(xaxis, yaxis, downsampled, vmin=bot, vmax=top)
    except ValueError:  # emptry array
        return
    ax.set_aspect('auto')
    ax.set_ylabel('Cell ID')
    ax.set_xlabel('Time (sec)')
    ax.set_title(f"dF/F Heatmap for epoch {epoch}")


def display_cell_excerpts_over_time(results_file: pathlib.Path, tif: pathlib.Path, 
                                    indices=slice(None), num_to_display=20,
                                    cell_radius=5, data_channel=TiffChannels.ONE,
                                    number_of_channels=2, title='Cell Excerpts Over Time'):
    """ 
    Display cells as they fluoresce during the recording time, each cell in its
    own row, over time.
    Parameters:
    -----------
        results_file (pathlib.Path): Path to a results.npz file.
        tif (pathlib.Path): Path to the corresponding raw tiff recording.
        indices (slice or np.ndarray): List of indices of the relevant cells to look at.
        num_to_display (int): We usually have too many cells to display them all nicely.
        cell_radius (int): Number of pixels in the cell's radius.
        data_channel (Tiffchannels):  The channel containing the functional data.
        number_of_channels (int): Number of data channels.
    """
    res_data = np.load(results_file)
    relevant_indices = res_data['idx_components'][indices][:num_to_display]
    coords = res_data['crd'][relevant_indices]

    with tifffile.TiffFile(tif, movie=True) as f:
        data = f.asarray(slice(data_channel.value, None, number_of_channels))
        fps = f.scanimage_metadata['FrameData']['SI.hRoiManager.scanFrameRate']

    cell_coms = [coords[idx]['CoM'].astype(np.uint16)-1 for idx in range(len(coords))]
    shape = data.shape[1:]
    masks = [skimage.draw.rectangle(cell, extent=cell_radius*2, shape=shape) for cell in cell_coms]
    cell_data = [data[:, mask[0], mask[1]] for mask in masks]
    
    # Start plotting
    idx_sample_start = np.linspace(start=0, stop=data.shape[0], endpoint=False,
                                   num=num_to_display, dtype=np.uint64)
    idx_sample_end = idx_sample_start + np.uint64(5)
    w, h = matplotlib.figure.figaspect(1.)
    fig, axes = plt.subplots(len(cell_data), num_to_display, figsize=(w, h))
    for row_idx, (ax, cell) in enumerate(zip(axes, cell_data)):
        for col_idx, (frame_idx_start, frame_idx_end) in enumerate(zip(idx_sample_start,
                                                                       idx_sample_end)):
            ax[col_idx].imshow(cell[frame_idx_start:frame_idx_end, ...].mean(0), cmap='gray')
            ax[col_idx].spines['top'].set_visible(False)
            ax[col_idx].spines['bottom'].set_visible(False)
            ax[col_idx].spines['left'].set_visible(False)
            ax[col_idx].spines['right'].set_visible(False)
            ax[col_idx].set_xticks([])
            ax[col_idx].set_yticks([])
            ax[col_idx].set_frame_on(False)
    # Add labels to row and column at the edge
    for sample_idx, ax in zip(idx_sample_start, axes[-1, :]):
        ax.set_xticks([cell_radius])
        label = f'{sample_idx/fps:.1f}'
        ax.set_xticklabels([label])
    
    for cell_idx, ax in zip(np.arange(1, len(cell_data) + 1), axes[:, 0]):
        ax.set_yticks([cell_radius])
        ax.set_yticklabels([cell_idx])
    
    fig.suptitle(title)
    fig.subplots_adjust(hspace=0.05, wspace=0.05)
    fig.text(0.5, 0.05, 'Time (sec)', horizontalalignment='center')
    fig.text(0.07, 0.5, 'Cell ID', verticalalignment='center', rotation='vertical')
    fig.savefig(f'cell_mosaic_{title}.pdf', frameon=False, transparent=True)


def draw_rois_over_cells(fname: pathlib.Path):
    """ 
    Draw ROIs around cells in the FOV, and mark their number (ID).
    Parameters:
        fname (pathlib.Path): Original TIF filename.
    """
    assert fname.exists()
    try:
        results_file = next(fname.parent.glob(fname.name[:-4] + '*results.npz'))
    except StopIteration:
        print("Results file not found. Exiting.")
        return
    
    full_dict = np.load(results_file)
    rel_crds = full_dict['crd'][full_dict['idx_components']]
    fig, ax_img = plt.subplots()
    print("Reading TIF")
    data = tifffile.imread(str(fname)).mean(0)
    ax_img.imshow(data, cmap='gray')
    colors = [f'C{idx}' for idx in range(10)]
    for idx, item in enumerate(rel_crds):
        cur_coor = item['coordinates']
        # assert bounding box size
        bbox_x = np.abs(item['bbox'][0] - item['bbox'][2])
        bbox_y = np.abs(item['bbox'][1] - item['bbox'][3])
        if bbox_x * bbox_y == 0.:
            continue
        # Drop nans and draw
        cur_coor = cur_coor[~np.isnan(cur_coor)].reshape((-1, 2))
        ax_img.plot(cur_coor[:, 0], cur_coor[:, 1], colors[idx % 10])
        min_c, max_c = cur_coor[:, 0].max(), cur_coor[:, 1].max()
        ax_img.text(min_c, max_c, str(idx+1), color='w')


if __name__ == '__main__':
    tif_folder = pathlib.Path('/data/David/Vascular occluder_ALL/vip_td_gcamp_vasc_occ_280818/')
    tifs = tif_folder.rglob('f*0[1-9].tif')
    for tif in tifs:
        print(tif)
        try:
            result = next(tif_folder.rglob(tif.name[:-4] + '*results.npz'))
        except StopIteration:
            print("No results for prev TIF")
            continue
        print(f"Found: {result}")
        mask = display_cell_excerpts_over_time(result, tif, title=tif.name)
    plt.show(block=True)

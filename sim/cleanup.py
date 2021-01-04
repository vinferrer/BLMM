import warnings as w
# This warning is caused by numpy updates and should
# be ignored for now.
w.simplefilter(action = 'ignore', category = FutureWarning)
import numpy as np
import scipy
import scipy.sparse
import nibabel as nib
import sys
import os
import glob
import shutil
import yaml
from scipy import ndimage
import time
import pandas as pd
from lib.fileio import *
from matplotlib import pyplot as plt


# ===========================================================================
#
# Inputs:
#
# ---------------------------------------------------------------------------
#
# ===========================================================================
def cleanup(OutDir,simNo):

    # -----------------------------------------------------------------------
    # Get simulation directory
    # -----------------------------------------------------------------------
    # Simulation directory
    simDir = os.path.join(OutDir, 'sim' + str(simNo))

    # -----------------------------------------------------------------------
    # Create results directory (if we are on the first simulation)
    # -----------------------------------------------------------------------
    # Results directory
    resDir = os.path.join(OutDir,'results')

    # If resDir doesn't exist, make it
    if not os.path.exists(resDir):
        os.mkdir(resDir)

    # -----------------------------------------------------------------------
    # Read in design in BLMM inputs form (this just is easier as code already
    # exists for using this format).
    # -----------------------------------------------------------------------
    # There should be an inputs file in each simulation directory
    with open(os.path.join(simDir,'inputs.yml'), 'r') as stream:
        inputs = yaml.load(stream,Loader=yaml.FullLoader)

    # -----------------------------------------------------------------------
    # Get number of random effects, levels and random factors in design
    # -----------------------------------------------------------------------
    # Random factor variables.
    rfxmats = inputs['Z']

    # Number of random effects
    r = len(rfxmats)

    # Number of random effects for each factor, q
    nraneffs = []

    # Number of levels for each factor, l
    nlevels = []

    for k in range(r):

        rfxdes = loadFile(rfxmats[k]['f' + str(k+1)]['design'])
        rfxfac = loadFile(rfxmats[k]['f' + str(k+1)]['factor'])

        nraneffs = nraneffs + [rfxdes.shape[1]]
        nlevels = nlevels + [len(np.unique(rfxfac))]

    # Get number of random effects
    nraneffs = np.array(nraneffs)
    nlevels = np.array(nlevels)
    q = np.sum(nraneffs*nlevels)

    # Number of covariance parameters
    ncov = np.sum(nraneffs*(nraneffs+1)//2)

    # -----------------------------------------------------------------------
    # Get number of observations and fixed effects
    # -----------------------------------------------------------------------
    X = pd.io.parsers.read_csv(os.path.join(simDir,"data","X.csv"), header=None).values
    n = X.shape[0]
    p = X.shape[1]

    # -----------------------------------------------------------------------
    # Get number voxels and dimensions
    # -----------------------------------------------------------------------

    # nmap location 
    nmap = os.path.join(simDir, "BLMM", "blmm_vox_n.nii")

    # Work out dim if we don't already have it
    dim = nib.Nifti1Image.from_filename(nmap, mmap=False).shape[:3]

    # Work out affine
    affine = nib.Nifti1Image.from_filename(nmap, mmap=False).affine.copy()

    # Number of voxels
    v = np.prod(dim)

    # Delete nmap
    del nmap

    # -----------------------------------------------------------------------
    # Remove data directory
    # -----------------------------------------------------------------------
    shutil.rmtree(os.path.join(simDir, 'data'))

    # -----------------------------------------------------------------------
    # Convert R files to NIFTI images
    # -----------------------------------------------------------------------

    # Number of voxels in each batch
    nvb = 1000

    # Work out number of groups we have to split indices into.
    nvg = int(v//nvb)
    
    # Split voxels we want to look at into groups we can compute
    voxelGroups = np.array_split(np.arange(v), nvg)

    # Loop through each file reading in one at a time and adding to nifti
    for cv in np.arange(nvg):

        # Current group of voxels
        inds_cv = voxelGroups[cv]

        # Number of voxels currently
        v_current = len(inds_cv)

        # -------------------------------------------------------------------
        # Beta combine
        # -------------------------------------------------------------------

        # Read in file
        beta_current = pd.io.parsers.read_csv(os.path.join(simDir, 'lmer', 'beta_' + str(cv) + '.csv')).values

        print('beta_current shape', beta_current.shape)

        # Loop through parameters adding them one voxel at a time
        for param in np.arange(p):

            # Add back to a NIFTI file
            addBlockToNifti(os.path.join(simDir,"lmer","lmer_vox_beta.nii"), beta_current[:,param], inds_cv, volInd=param,dim=(*dim,int(p)))

        # Remove file
        os.remove(os.path.join(simDir, 'lmer', 'beta_' + str(cv) + '.csv'))

        # -------------------------------------------------------------------
        # Sigma2 combine
        # -------------------------------------------------------------------

        # Read in file
        sigma2_current = pd.io.parsers.read_csv(os.path.join(simDir, 'lmer', 'sigma2_' + str(cv) + '.csv')).values

        # Add back to a NIFTI file
        addBlockToNifti(os.path.join(simDir,"lmer","lmer_vox_sigma2.nii"), sigma2_current, inds_cv, volInd=0,dim=(*dim,1))

        # Remove file
        os.remove(os.path.join(simDir, 'lmer', 'sigma2_' + str(cv) + '.csv'))

        # -------------------------------------------------------------------
        # vechD combine
        # -------------------------------------------------------------------

        # Read in file
        vechD_current = pd.io.parsers.read_csv(os.path.join(simDir, 'lmer', 'vechD_' + str(cv) + '.csv')).values

        # Loop through covariance parameters adding them one voxel at a time
        for param in np.arange(ncov):

            # Add back to a NIFTI file
            addBlockToNifti(os.path.join(simDir,"lmer","lmer_vox_D.nii"), vechD_current[:,param], inds_cv, volInd=param,dim=(*dim,int(ncov)))

        # Remove file
        os.remove(os.path.join(simDir, 'lmer', 'vechD_' + str(cv) + '.csv'))

        # -------------------------------------------------------------------
        # Log-likelihood combine
        # -------------------------------------------------------------------

        # Read in file
        llh_current = pd.io.parsers.read_csv(os.path.join(simDir, 'lmer', 'llh_' + str(cv) + '.csv')).values

        # Add back to a NIFTI file
        addBlockToNifti(os.path.join(simDir,"lmer","lmer_vox_llh.nii"), llh_current, inds_cv, volInd=0,dim=(*dim,1))

        # Remove file
        os.remove(os.path.join(simDir, 'lmer', 'llh_' + str(cv) + '.csv'))

        # -------------------------------------------------------------------
        # Times combine
        # -------------------------------------------------------------------

        # Read in file
        times_current = pd.io.parsers.read_csv(os.path.join(simDir, 'lmer', 'times_' + str(cv) + '.csv')).values

        # Add back to a NIFTI file
        addBlockToNifti(os.path.join(simDir,"lmer","lmer_vox_times.nii"), times_current, inds_cv, volInd=0,dim=(*dim,1))

        # Remove file
        os.remove(os.path.join(simDir, 'lmer', 'times_' + str(cv) + '.csv'))

    # -----------------------------------------------------------------------
    # Remove BLMM maps we are not interested in (for memory purposes)
    # -----------------------------------------------------------------------
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_con.nii'))
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_conSE.nii'))
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_conT.nii'))
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_conT_swedf.nii'))
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_edf.nii'))
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_mask.nii'))
    os.remove(os.path.join(simDir, 'BLMM', 'blmm_vox_n.nii'))

    # -----------------------------------------------------------------------
    # MAE and MRD for beta maps
    # -----------------------------------------------------------------------

    # Get BLMM beta
    beta_blmm = nib.load(os.path.join(simDir, 'BLMM', 'blmm_vox_beta.nii')).get_data()

    # Get lmer beta
    beta_lmer = nib.load(os.path.join(simDir, 'lmer', 'lmer_vox_beta.nii')).get_data()

    # Remove zero values
    beta_blmm = beta_blmm[beta_lmer!=0]
    beta_lmer = beta_lmer[beta_lmer!=0]

    # Get MAE
    MAE_beta = np.mean(np.abs(beta_blmm-beta_lmer))

    # Get MRD
    MRD_beta = np.mean(2*np.abs((beta_blmm-beta_lmer)/(beta_blmm+beta_lmer)))

    # Make line to add to csv for MAE
    MAE_beta_line = np.array([[simNo, MAE_beta]])

    # Make line to add to csv for MRD
    MRD_beta_line = np.array([[simNo, MRD_beta]])

    # MAE beta file name
    fname_MAE = os.path.join(resDir, 'MAE_beta.csv')

    # MRD beta file name
    fname_MRD = os.path.join(resDir, 'MRD_beta.csv')

    # Add to files 
    addLineToCSV(fname_MAE, MAE_beta_line)
    addLineToCSV(fname_MRD, MRD_beta_line)

    # Cleanup
    del beta_lmer, beta_blmm, MAE_beta, MRD_beta, MAE_beta_line, MRD_beta_line

    # -----------------------------------------------------------------------
    # MAE and MRD for sigma2 maps
    # -----------------------------------------------------------------------

    # Get BLMM sigma2
    sigma2_blmm = nib.load(os.path.join(simDir, 'BLMM', 'blmm_vox_sigma2.nii')).get_data()

    # Get lmer sigma2
    sigma2_lmer = nib.load(os.path.join(simDir, 'lmer', 'lmer_vox_sigma2.nii')).get_data()

    # Remove zero values
    sigma2_blmm = sigma2_blmm[sigma2_lmer!=0]
    sigma2_lmer = sigma2_lmer[sigma2_lmer!=0]

    # Get MAE
    MAE_sigma2 = np.mean(np.abs(sigma2_blmm-sigma2_lmer))

    # Get MRD
    MRD_sigma2 = np.mean(2*np.abs((sigma2_blmm-sigma2_lmer)/(sigma2_blmm+sigma2_lmer)))

    # Make line to add to csv for MAE
    MAE_sigma2_line = np.array([[simNo, MAE_sigma2]])

    # Make line to add to csv for MRD
    MRD_sigma2_line = np.array([[simNo, MRD_sigma2]])

    # MAE sigma2 file name
    fname_MAE = os.path.join(resDir, 'MAE_sigma2.csv')

    # MRD sigma2 file name
    fname_MRD = os.path.join(resDir, 'MRD_sigma2.csv')

    # Add to files 
    addLineToCSV(fname_MAE, MAE_sigma2_line)
    addLineToCSV(fname_MRD, MRD_sigma2_line)

    # Cleanup
    del sigma2_lmer, sigma2_blmm, MAE_sigma2, MRD_sigma2, MAE_sigma2_line, MRD_sigma2_line

    # -----------------------------------------------------------------------
    # MAE and MRD for vechD maps
    # -----------------------------------------------------------------------

    # Get BLMM vechD
    vechD_blmm = nib.load(os.path.join(simDir, 'BLMM', 'blmm_vox_D.nii')).get_data()

    # Get lmer vechD
    vechD_lmer = nib.load(os.path.join(simDir, 'lmer', 'lmer_vox_D.nii')).get_data()

    # Remove zero values
    vechD_blmm = vechD_blmm[vechD_lmer!=0]
    vechD_lmer = vechD_lmer[vechD_lmer!=0]

    # Get MAE
    MAE_vechD = np.mean(np.abs(vechD_blmm-vechD_lmer))

    # Get MRD
    MRD_vechD = np.mean(2*np.abs((vechD_blmm-vechD_lmer)/(vechD_blmm+vechD_lmer)))

    # Make line to add to csv for MAE
    MAE_vechD_line = np.array([[simNo, MAE_vechD]])

    # Make line to add to csv for MRD
    MRD_vechD_line = np.array([[simNo, MRD_vechD]])

    # MAE vechD file name
    fname_MAE = os.path.join(resDir, 'MAE_vechD.csv')

    # MRD vechD file name
    fname_MRD = os.path.join(resDir, 'MRD_vechD.csv')

    # Add to files 
    addLineToCSV(fname_MAE, MAE_vechD_line)
    addLineToCSV(fname_MRD, MRD_vechD_line)

    # Cleanup
    del vechD_lmer, vechD_blmm, MAE_vechD, MRD_vechD, MAE_vechD_line, MRD_vechD_line

    # -----------------------------------------------------------------------
    # Log-likelihood difference
    # -----------------------------------------------------------------------

    # Get BLMM llh
    llh_blmm = nib.load(os.path.join(simDir, 'BLMM', 'blmm_vox_llh.nii')).get_data()

    # Get lmer llh
    llh_lmer = nib.load(os.path.join(simDir, 'lmer', 'lmer_vox_llh.nii')).get_data()

    # Remove zero values
    llh_blmm = llh_blmm[llh_lmer!=0]
    llh_lmer = llh_lmer[llh_lmer!=0]

    # Get maximum absolute difference
    MAD_llh = np.mean(np.abs(llh_blmm-llh_lmer))

    # Make line to add to csv for MAD
    MAD_llh_line = np.array([[simNo, MAD_llh]])

    # MAD llh file name
    fname_MAD = os.path.join(resDir, 'MAD_llh.csv')

    # Add to files 
    addLineToCSV(fname_MAD, MAD_llh_line)

    # Cleanup
    del llh_lmer, llh_blmm, MAD_llh, MAD_llh_line

    
    # -----------------------------------------------------------------------
    # Times
    # -----------------------------------------------------------------------

    # Get BLMM times
    times_blmm = nib.load(os.path.join(simDir, 'BLMM', 'blmm_vox_times.nii')).get_data()

    # Get lmer times
    times_lmer = nib.load(os.path.join(simDir, 'lmer', 'lmer_vox_times.nii')).get_data()

    # Remove zero values
    times_blmm = times_blmm[times_lmer!=0]
    times_lmer = times_lmer[times_lmer!=0]

    # Get mean difference
    MD_times = np.mean(times_lmer-times_blmm)

    # Get total difference
    TD_times = np.sum(times_lmer-times_blmm)

    # Make line to add to csv for MD
    MD_times_line = np.array([[simNo, MD_times]])

    # Make line to add to csv for TD
    TD_times_line = np.array([[simNo, TD_times]])

    # MD times file name
    fname_MD = os.path.join(resDir, 'MD_times.csv')

    # TD times file name
    fname_TD = os.path.join(resDir, 'TD_times.csv')

    # Add to files 
    addLineToCSV(fname_MD, MD_times_line)

    # Add to files 
    addLineToCSV(fname_TD, TD_times_line)

    # Cleanup
    del times_lmer, times_blmm, MD_times, MD_times_line, TD_times, TD_times_line


    # -----------------------------------------------------------------------
    # P value counts for histograms
    # -----------------------------------------------------------------------
    # Load logp map
    logp = nib.load(os.path.join(simDir, 'BLMM', 'blmm_vox_conTlp.nii')).get_data()

    # Remove zeros
    logp = logp[logp!=0]

    # Un-"log"
    p = 10**(-logp)

    # Get bin counts
    counts,_,_=plt.hist(p, bins=100, label='hist')

    # Make line to add to csv for bin counts
    pval_line = np.concatenate((np.array([[simNo]]),np.array([counts])),axis=1)

    # pval file name
    fname_pval = os.path.join(resDir, 'pval_counts.csv')

    # Add to files 
    addLineToCSV(fname_pval, pval_line)

    # Save histogram
    plt.savefig(os.path.join(simDir, 'BLMM', 'pValHist.png'))

    # Cleanup
    del p, logp, counts, fname_pval, pval_line

    # -----------------------------------------------------------------------
    # Cleanup finished!
    # -----------------------------------------------------------------------

    print('----------------------------------------------------------------')
    print('Simulation instance ' + str(simNo) + ' complete!')
    print('----------------------------------------------------------------')

# This function adds a line to a csv. If the csv does not exist it creates it.
# It uses a filelock system
def addLineToCSV(fname, line):

    # Check if file is in use
    fileLocked = True
    while fileLocked:
        try:
            # Create lock file, so other jobs know we are writing to this file
            f = os.open(fname + ".lock", os.O_CREAT|os.O_EXCL|os.O_RDWR)
            fileLocked = False
        except FileExistsError:
            fileLocked = True

    # Check if file already exists and if so read it in
    if os.path.isfile(fname):

        # Read in data
        data = pd.io.parsers.read_csv(fname, header=None, index_col=None).values

        # Append line to data
        data = np.concatenate((data, line),axis=0)

    else:

        # The data is just this line
        data = line

    # Write data back to file
    pd.DataFrame(data).to_csv(fname, header=None, index=None)

    # Delete lock file, so other jobs know they can now write to the
    # file
    os.remove(fname + ".lock")
    os.close(f)

    del fname

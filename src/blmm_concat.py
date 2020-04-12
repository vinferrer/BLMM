import warnings as w
# This warning is caused by numpy updates and should
# be ignored for now.
w.simplefilter(action = 'ignore', category = FutureWarning)
import numpy as np
import nibabel as nib
import sys
import os
import glob
import shutil
import yaml
import time
np.set_printoptions(threshold=np.nan)
from lib.npMatrix3d import *
from lib.npMatrix2d import *
from lib.fileio import *
import src.blmm_inference as blmm_inference
import src.blmm_estimate as blmm_estimate

# ====================================================================================
#
# This file is the third stage of the BLMM pipeline. This stage reads in the product
# matrices output by each of the `blmm_batch` jobs during the second stage and s 
# them to obtain the product matrices for the overall model. It also calculates n_sv
# for the whole model and the overall mask.
#
# Following this, the `blmm_concat` code then seperates the voxels in the brain into
# two categories; "inner" and "ring" (explained in the developer notes below). Once
# this has been done the product matrices corresponding to "inner" and "ring" voxels
# are passed to `blmm_estimate`, which estimates the parameters of the model; beta,
# sigma2 and D. Following this, the product matrices and parameter estimates are 
# passed to `blmm_inference`, which generates statistic maps and other miscelanoues 
# output.
#
# ------------------------------------------------------------------------------------
#
# Author: Tom Maullin (Last edited: 04/04/2020)
#
# ------------------------------------------------------------------------------------
#
# The code takes the following inputs:
#
#  - input path (optional): If specified, the first argument will be ased to be a
#                           path to an `inputs` yml file, following the same 
#                           formatting guidelines as `blmm_config.yml`. If not 
#                           specified, the default file `blmm_config.yml` will be 
#                           ased to contain the inputs.
#
# ------------------------------------------------------------------------------------
# Developer notes:
# ------------------------------------------------------------------------------------
# In the following code I have used the following subscripts to indicate:
#
# _r - This means this is an array of values corresponding to voxels which
#      are present in between k and n-1 studies (inclusive), where k is
#      decided by the user specified thresholds. These voxels will typically
#      be on the edge of the brain and look like a "ring" around the brain,
#      hence "_r" for ring.
# 
# _i - This means that this is an array of values corresponding to voxels 
#      which are present in all n studies. These will usually look like
#      a smaller mask place inside the whole study mask. Hence "_i" for 
#      inner.
#
# _sv - This means this variable is spatially varying (There is a reading
#       per voxel). 
#
# ====================================================================================
def main(*args):

    # --------------------------------------------------------------------------------
    # Check inputs
    # --------------------------------------------------------------------------------
    if len(args)==0 or (not args[0]):
        # Load in inputs
        with open(os.path.join(
                    os.path.dirname(os.path.realpath(__file__)),
                    '..',
                    'blmm_config.yml'), 'r') as stream:
            inputs = yaml.load(stream,Loader=yaml.FullLoader)
    else:
        if type(args[0]) is str:
            # In this case inputs file is first argument
            with open(os.path.join(args[0]), 'r') as stream:
                inputs = yaml.load(stream,Loader=yaml.FullLoader)
        else:  
            # In this case inputs structure is first argument.
            inputs = args[0]

    # --------------------------------------------------------------------------------
    # Read basic inputs
    # --------------------------------------------------------------------------------
    OutDir = inputs['outdir']

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

    # Get number of unique random effects
    q_u = np.sum(nraneffs*(nraneffs+1)//2)
    
    # Get number of parameters
    L1 = str2vec(inputs['contrasts'][0]['c' + str(1)]['vector'])
    L1 = np.array(L1)
    p = L1.shape[0]
    del L1
    
    # Read in the nifti size and work out number of voxels.
    with open(inputs['Y_files']) as a:
        nifti_path = a.readline().replace('\n', '')
        nifti = loadFile(nifti_path)

    NIFTIsize = nifti.shape
    v = int(np.prod(NIFTIsize))

    # --------------------------------------------------------------------------------
    # Remove any files from the previous runs
    #
    # Note: This is important as if we are outputting blocks to files we want to be
    # sure none of the previous results are lingering around anywhere.
    # --------------------------------------------------------------------------------

    files = ['blmm_vox_n.nii', 'blmm_vox_mask.nii', 'blmm_vox_edf.nii', 'blmm_vox_beta.nii',
             'blmm_vox_llh.nii', 'blmm_vox_sigma2.nii', 'blmm_vox_D.nii', 'blmm_vox_resms.nii',
             'blmm_vox_cov.nii', 'blmm_vox_conT_swedf.nii', 'blmm_vox_conT.nii', 'blmm_vox_conTlp.nii',
             'blmm_vox_conSE.nii', 'blmm_vox_con.nii', 'blmm_vox_conF.nii', 'blmm_vox_conF_swedf.nii',
             'blmm_vox_conFlp.nii', 'blmm_vox_conR2.nii']

    for file in files:

        if os.path.exists(os.path.join(OutDir, file)):

            os.remove(os.path.join(OutDir, file))

    # --------------------------------------------------------------------------------
    # Get n (number of observations) and n_sv (spatially varying number of
    # observations)
    # --------------------------------------------------------------------------------

    # Work out number of batchs
    n_b = len(glob.glob(os.path.join(OutDir,"tmp","blmm_vox_n_batch*")))

    if (len(args)==0) or (type(args[0]) is str):

        # Read in n (spatially varying)
        nmapb  = loadFile(os.path.join(OutDir,"tmp", "blmm_vox_n_batch1.nii"))
        n_sv = nmapb.get_data()# Read in uniqueness Mask file

        # Remove files, don't need them anymore
        os.remove(os.path.join(OutDir,"tmp","blmm_vox_n_batch1.nii"))

        # Cycle through batches and add together n.
        for batchNo in range(2,(n_b+1)):
            
            # Obtain the full nmap.
            n_sv = n_sv + loadFile(os.path.join(OutDir,"tmp", 
                "blmm_vox_n_batch" + str(batchNo) + ".nii")).get_data()
            
            # Remove file, don't need it anymore
            os.remove(os.path.join(OutDir, "tmp", "blmm_vox_n_batch" + str(batchNo) + ".nii"))

    else:
        # Read in n_sv.
        n_sv = args[4]

    # Save nmap
    nmap = nib.Nifti1Image(n_sv,
                           nifti.affine,
                           header=nifti.header)
    nib.save(nmap, os.path.join(OutDir,'blmm_vox_n.nii'))
    n_sv = n_sv.reshape(v, 1)
    del nmap

    # Get ns.
    X = loadFile(inputs['X'])
    n = X.shape[0]

    # --------------------------------------------------------------------------------
    # Create Mask
    # --------------------------------------------------------------------------------

    Mask = np.ones([v, 1])

    # Check for user specified missingness thresholds.
    if 'Missingness' in inputs:

        # Apply user specified missingness thresholding.
        if ("MinPercent" in inputs["Missingness"]) or ("minpercent" in inputs["Missingness"]):

            # Read in relative threshold
            if "MinPercent" in inputs["Missingness"]:
                rmThresh = inputs["Missingness"]["MinPercent"]
            else:
                rmThresh = inputs["Missingness"]["minpercent"]

            # If it's a percentage it will be a string and must be converted.
            rmThresh = str(rmThresh)
            if "%" in rmThresh:
                rmThresh = float(rmThresh.replace("%", ""))/100
            else:
                rmThresh = float(rmThresh)

            # Check the Relative threshold is between 0 and 1.
            if (rmThresh < 0) or (rmThresh > 1):
                raise ValueError('Minumum percentage missingness threshold is out of range: ' +
                                 '0 < ' + str(rmThresh) + ' < 1 violation')

            # Mask based on threshold.
            Mask[n_sv<rmThresh*n]=0

        if ("MinN" in inputs["Missingness"]) or ("minn" in inputs["Missingness"]):

            # Read in relative threshold
            if "minn" in inputs["Missingness"]:
                amThresh = inputs["Missingness"]["minn"]
            else:
                amThresh = inputs["Missingness"]["MinN"]

            # If it's a percentage it will be a string and must be converted.
            if isinstance(amThresh, str):
                amThresh = float(amThresh)

            # Mask based on threshold.
            Mask[n_sv<amThresh]=0

    # We remove anything with 1 degree of freedom (or less) by default.
    # 1 degree of freedom seems to cause broadcasting errors on a very
    # small percentage of voxels.
    Mask[n_sv<=p+1]=0

    if 'analysis_mask' in inputs:

        amask_path = inputs["analysis_mask"]
        
        # Read in the mask nifti.
        amask = loadFile(amask_path).get_data().reshape([v,1])

    else:

        # By default make amask ones
        amask = np.ones([v,1])

    # Get indices for block. These indices have to be the indices we want to
    # compute, in relation to the entire volume.
    bamInds = get_amInds(amask, 4, 10)

    # Get indices for analysis mask. These indices have to be the indices we
    # have recorded for the product matrices with respect to the entire volume
    amInds = get_amInds(amask)

    # Ensure overall mask matches analysis mask
    Mask[~np.in1d(np.arange(v).reshape(v,1), bamInds)]=0
        
    # Output final mask map
    maskmap = nib.Nifti1Image(Mask.reshape(
                                    NIFTIsize[0],
                                    NIFTIsize[1],
                                    NIFTIsize[2]
                                    ),
                              nifti.affine,
                              header=nifti.header)
    nib.save(maskmap, os.path.join(OutDir,'blmm_vox_mask.nii'))
    del maskmap

    # Get indices of voxels in ring around brain where there are
    # missing studies.
    R_inds = np.sort(np.where((Mask==1)*(n_sv<n))[0])#

    Mask2 = np.ones(Mask.shape)
    Mask2[~np.in1d(np.arange(v).reshape(v,1), R_inds)]=0

    # Output final mask map
    maskmap = nib.Nifti1Image(Mask2.reshape(
                                    NIFTIsize[0],
                                    NIFTIsize[1],
                                    NIFTIsize[2]
                                    ),
                              nifti.affine,
                              header=nifti.header)
    nib.save(maskmap, os.path.join(OutDir,'blmm_vox_mask2.nii'))
    del maskmap

    # Work out the 'ring' indices, in relation to the analysis mask
    ix_r = np.argsort(np.argsort(R_inds))
    R_inds_am = np.sort(np.where(np.in1d(amInds,R_inds))[0])[ix_r]

    # Get indices of the "inner" volume where all studies had information
    # present. I.e. the voxels (usually near the middle of the brain) where
    # every voxel has a reading for every study.
    I_inds = np.sort(np.where((Mask==1)*(n_sv==n))[0])#

    # Work out the 'inner' indices, in relation to the analysis mask
    ix_i = np.argsort(np.argsort(I_inds))
    I_inds_am = np.sort(np.where(np.in1d(amInds,I_inds))[0])[ix_i]
    del Mask

    # --------------------------------------------------------------------------------
    # Move to working with only analysis mask indices
    # --------------------------------------------------------------------------------

    # Number of voxels in ring
    v_r = R_inds.shape[0]

    # Number of voxels in inner mask
    v_i = I_inds.shape[0]

    # Number of voxels in whole (inner + ring) mask
    v_m = v_i + v_r

    # Create df map
    df_r = n_sv[R_inds,:] - p
    df_r = df_r.reshape([v_r])
    df_i = n - p

    # Unmask df
    df = np.zeros([v])
    df[R_inds] = df_r 
    df[I_inds] = df_i

    df = df.reshape(int(NIFTIsize[0]),
                    int(NIFTIsize[1]),
                    int(NIFTIsize[2]))

    # Save beta map.
    dfmap = nib.Nifti1Image(df,
                            nifti.affine,
                            header=nifti.header)
    nib.save(dfmap, os.path.join(OutDir,'blmm_vox_edf.nii'))
    del df, dfmap

    # --------------------------------------------------------------------------------
    # Load X'X, X'Y, Y'Y, X'Z, Y'Z, Z'Z
    # --------------------------------------------------------------------------------

    # Ring X'Y, Y'Y, Z'Y
    XtY_r = readAndSumAtB('XtY',OutDir,R_inds_am,n_b).reshape([v_r, p, 1])
    YtY_r = readAndSumAtB('YtY',OutDir,R_inds_am,n_b).reshape([v_r, 1, 1])
    ZtY_r = readAndSumAtB('ZtY',OutDir,R_inds_am,n_b).reshape([v_r, q, 1])

    # Inner X'Y, Y'Y, Z'Y
    XtY_i = readAndSumAtB('XtY',OutDir,I_inds_am,n_b).reshape([v_i, p, 1])
    YtY_i = readAndSumAtB('YtY',OutDir,I_inds_am,n_b).reshape([v_i, 1, 1])
    ZtY_i = readAndSumAtB('ZtY',OutDir,I_inds_am,n_b).reshape([v_i, q, 1])

    ZtZ2_i = readAndSumUniqueAtB('ZtZ',OutDir,I_inds,n_b,False).reshape([1, q, q])
    ZtZ2_r = readAndSumUniqueAtB('ZtZ',OutDir,R_inds,n_b,False).reshape([v_r, q, q])

    # Work out the uniqueness mask for the spatially varying designs
    uniquenessMask = loadFile(os.path.join(OutDir,"tmp", 
        "blmm_vox_uniqueM_batch1.nii")).get_data().reshape(v)

    # Work out the uniqueness mask inside the ring around the brain
    uniquenessMask_r = uniquenessMask[R_inds]

    # Work out the uniqueness mask value inside the inner part of the brain
    uniquenessMask_i = uniquenessMask[I_inds[0]] 

    maxM = np.int32(np.amax(uniquenessMask))

    # read in XtX, ZtX, ZtZ
    ZtZ_batch_unique = np.load(
        os.path.join(OutDir,"tmp","ZtZ1.npy"))
    ZtX_batch_unique = np.load(
        os.path.join(OutDir,"tmp","ZtX1.npy"))
    XtX_batch_unique = np.load(
        os.path.join(OutDir,"tmp","XtX1.npy"))

    # Make zeros for outer ring of brain ZtZ, XtX, ZtX etc
    ZtZ_batch_r = np.zeros((v_r, ZtZ_batch_unique.shape[1]))
    ZtX_batch_r = np.zeros((v_r, ZtX_batch_unique.shape[1]))
    XtX_batch_r = np.zeros((v_r, XtX_batch_unique.shape[1]))

    # Fill with unique maskings
    for m in range(1,maxM+1):

        # Work out Z'Z, Z'X and X'X for the ring
        ZtZ_batch_r[np.where(uniquenessMask_r==m),:] = ZtZ_batch_unique[(m-1),:]
        ZtX_batch_r[np.where(uniquenessMask_r==m),:] = ZtX_batch_unique[(m-1),:]
        XtX_batch_r[np.where(uniquenessMask_r==m),:] = XtX_batch_unique[(m-1),:]

        # Work out Z'Z, Z'X and X'X for the inner
        if uniquenessMask_i == m:
            ZtZ_batch_i = ZtZ_batch_unique[(m-1),:]
            ZtX_batch_i = ZtX_batch_unique[(m-1),:]
            XtX_batch_i = XtX_batch_unique[(m-1),:]

    # Perform summation for ring
    XtX_r = XtX_batch_r
    ZtX_r = ZtX_batch_r
    ZtZ_r = ZtZ_batch_r

    # Perform summation for inner
    XtX_i = XtX_batch_i
    ZtX_i = ZtX_batch_i
    ZtZ_i = ZtZ_batch_i

    # Cycle through batches and add together results.
    for batchNo in range(2,(n_b+1)):

        # Read in uniqueness Mask file
        uniquenessMask = loadFile(os.path.join(OutDir,"tmp", 
            "blmm_vox_uniqueM_batch" + str(batchNo) + ".nii")).get_data().reshape(v)

        # Work out the uniqueness mask inside the ring around the brain
        uniquenessMask_r = uniquenessMask[R_inds] 

        # Work out the uniqueness mask value inside the inner part of the brain
        uniquenessMask_i = uniquenessMask[I_inds[0]] 

        maxM = np.int32(np.amax(uniquenessMask))

        # read in XtX, ZtX, ZtZ
        ZtZ_batch_unique = np.load(
            os.path.join(OutDir,"tmp","ZtZ" + str(batchNo) + ".npy"))
        ZtX_batch_unique = np.load(
            os.path.join(OutDir,"tmp","ZtX" + str(batchNo) + ".npy"))
        XtX_batch_unique = np.load(
            os.path.join(OutDir,"tmp","XtX" + str(batchNo) + ".npy"))

        # Make zeros for whole nifti ZtZ, XtX, ZtX etc
        ZtZ_batch_r = np.zeros((v_r, ZtZ_batch_unique.shape[1]))
        ZtX_batch_r = np.zeros((v_r, ZtX_batch_unique.shape[1]))
        XtX_batch_r = np.zeros((v_r, XtX_batch_unique.shape[1]))

        # Fill with unique maskings
        for m in range(1,maxM+1):

            ZtZ_batch_r[np.where(uniquenessMask_r==m),:] = ZtZ_batch_unique[(m-1),:]
            ZtX_batch_r[np.where(uniquenessMask_r==m),:] = ZtX_batch_unique[(m-1),:]
            XtX_batch_r[np.where(uniquenessMask_r==m),:] = XtX_batch_unique[(m-1),:]

            # Work out Z'Z, Z'X and X'X for the inner
            if uniquenessMask_i == m:

                ZtZ_batch_i = ZtZ_batch_unique[(m-1),:]
                ZtX_batch_i = ZtX_batch_unique[(m-1),:]
                XtX_batch_i = XtX_batch_unique[(m-1),:]

        # Add to running total
        XtX_r = XtX_r + XtX_batch_r
        ZtX_r = ZtX_r + ZtX_batch_r
        ZtZ_r = ZtZ_r + ZtZ_batch_r

        XtX_i = XtX_i + XtX_batch_i
        ZtX_i = ZtX_i + ZtX_batch_i
        ZtZ_i = ZtZ_i + ZtZ_batch_i
        
    # Delete the files as they are no longer needed.
    fileStrs = ["XtY","YtY","ZtY","XtX","ZtX","ZtZ"]

    for batchNo in range(1,(n_b+1)):
        # Remove uniqueness mask
        os.remove(os.path.join(OutDir, "tmp", "blmm_vox_uniqueM_batch" + str(batchNo) + ".nii"))

        for fileStr in fileStrs:
            # Remove product matrices
            os.remove(os.path.join(OutDir, "tmp",fileStr + str(batchNo) + ".npy"))

    # --------------------------------------------------------------------------------
    # Calculate betahat = (X'X)^(-1)X'Y and output beta maps
    # --------------------------------------------------------------------------------

    print('check')
    print(np.all(ZtZ2_i==ZtZ_i))
    print(np.all(ZtZ2_r==ZtZ_r))

    XtX_r = XtX_r.reshape([v_r, p, p])
    ZtX_r = ZtX_r.reshape([v_r, q, p])
    ZtZ_r = ZtZ_r.reshape([v_r, q, q])

    XtX_i = XtX_i.reshape([1, p, p])
    ZtX_i = ZtX_i.reshape([1, q, p])
    ZtZ_i = ZtZ_i.reshape([1, q, q])

    REML = False

    # If we have indices where only some studies are present, work out X'X and
    # X'Y for these studies. (Remember X'Y, Y'Y and Z'Y have already had the 
    # analysis mask applied to them during the batch stage)
    if v_r:

        # Spatially varying nv for ring
        n_sv_r = n_sv[R_inds,:]

        # Transposed matrices
        YtX_r = XtY_r.transpose(0,2,1)
        YtZ_r = ZtY_r.transpose(0,2,1) 
        XtZ_r = ZtX_r.transpose(0,2,1)

        # Run parameter estimation
        beta_r, sigma2_r, D_r = blmm_estimate.main(inputs, R_inds, XtX_r, XtY_r, XtZ_r, YtX_r, YtY_r, YtZ_r, ZtX_r, ZtY_r, ZtZ_r, n_sv_r, nlevels, nraneffs)

        # Run inference
        blmm_inference.main(inputs, nraneffs, nlevels, R_inds, beta_r, D_r, sigma2_r, n_sv_r, XtX_r, XtY_r, XtZ_r, YtX_r, YtY_r, YtZ_r, ZtX_r, ZtY_r, ZtZ_r)       
        
    if v_i:

        # Transposed matrices
        YtX_i = XtY_i.transpose(0,2,1)
        YtZ_i = ZtY_i.transpose(0,2,1) 
        XtZ_i = ZtX_i.transpose(0,2,1)

        # Run parameter estimation
        beta_i, sigma2_i, D_i = blmm_estimate.main(inputs, I_inds,  XtX_i, XtY_i, XtZ_i, YtX_i, YtY_i, YtZ_i, ZtX_i, ZtY_i, ZtZ_i, n, nlevels, nraneffs)

        # Run inference
        blmm_inference.main(inputs, nraneffs, nlevels, I_inds, beta_i, D_i, sigma2_i, n, XtX_i, XtY_i, XtZ_i, YtX_i, YtY_i, YtZ_i, ZtX_i, ZtY_i, ZtZ_i)

    # Clean up files
    if len(args)==0:
        os.remove(os.path.join(OutDir, 'nb.txt'))
    shutil.rmtree(os.path.join(OutDir, 'tmp'))

    w.resetwarnings()


def readAndSumAtB(AtBstr, OutDir, vinds,n_b):

    # Read in first A'B
    AtB = readLinesFromNPY(os.path.join(OutDir,"tmp",AtBstr + '1.npy'), vinds)

    # Cycle through batches and add together results.
    for batchNo in range(2,(n_b+1)):

        # Sum A'B
        AtB = AtB + readLinesFromNPY(os.path.join(OutDir,"tmp",AtBstr + str(batchNo) + ".npy"), vinds)

    # Return A'B
    return(AtB)


def readAndSumUniqueAtB(AtBstr, OutDir, vinds, n_b, sv):

    # Work out the uniqueness mask for the spatially varying designs
    uniquenessMask = loadFile(os.path.join(OutDir,"tmp", 
        "blmm_vox_uniqueM_batch1.nii")).get_data()

    v = np.prod(uniquenessMask.shape)
    vcurrent = np.prod(vinds.shape)

    uniquenessMask=uniquenessMask.reshape(v)

    # Work out how many unique matrices there were
    maxM = np.int32(np.amax(uniquenessMask))

    if sv:
        # Work out the uniqueness mask inside the ring around the brain
        uniquenessMask = uniquenessMask[vinds]
    else:
        # Work out the uniqueness mask value inside the inner part of the brain
        uniquenessMask = uniquenessMask[vinds[0]] 


    # read in XtX
    AtB_batch_unique = np.load(
        os.path.join(OutDir,"tmp",AtBstr+"1.npy"))

    # Make zeros for outer ring of brain ZtZ, XtX, ZtX etc (remember A'B is still flattened)
    if sv:
        AtB = np.zeros((vcurrent, AtB_batch_unique.shape[1]))
    else:
        AtB = np.zeros(AtB_batch_unique.shape[1])

    # Fill with unique maskings
    for m in range(1,maxM+1):

        if sv:
            # Work out Z'Z, Z'X and X'X for the ring
            AtB[np.where(uniquenessMask==m),:] = AtB_batch_unique[(m-1),:]

        # Work out Z'Z, Z'X and X'X for the inner
        else:
            if uniquenessMask == m:
                AtB = AtB_batch_unique[(m-1),:]

    # Cycle through batches and add together results.
    for batchNo in range(2,(n_b+1)):

        # Read in uniqueness Mask file
        uniquenessMask = loadFile(os.path.join(OutDir,"tmp", 
            "blmm_vox_uniqueM_batch" + str(batchNo) + ".nii")).get_data().reshape(v)

        maxM = np.int32(np.amax(uniquenessMask))

        if sv:
            # Work out the uniqueness mask inside the ring around the brain
            uniquenessMask = uniquenessMask[vinds] 
        else:
            # Work out the uniqueness mask value inside the inner part of the brain
            uniquenessMask = uniquenessMask[vinds[0]] 


        # read in XtX, ZtX, ZtZ
        AtB_batch_unique = np.load(
            os.path.join(OutDir,"tmp",AtBstr + str(batchNo) + ".npy"))

        # Make zeros for whole nifti ZtZ, XtX, ZtX etc
        AtB_batch = np.zeros((vcurrent, AtB_batch_unique.shape[1]))

        # Fill with unique maskings
        for m in range(1,maxM+1):

            AtB_batch[np.where(uniquenessMask==m),:] = AtB_batch_unique[(m-1),:]

            # Work out Z'Z, Z'X and X'X for the inner
            if uniquenessMask == m:

                AtB_batch = AtB_batch_unique[(m-1),:]

        # Add to running total
        AtB = AtB + AtB_batch

    return(AtB)


if __name__ == "__main__":
    main()
